"""Unit tests for the readiness router.

The app under test is built locally rather than through `create_app()`: the
router must be usable on its own, and this keeps the test independent of the
application factory.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tcg_api.config import REDIS_URL_ENV_VAR
from tcg_api.routers import readiness


@pytest.fixture
def client_factory():
    def build(
        database_reachable: bool = True, storage_reachable: bool = True, redis: str = "ok"
    ) -> TestClient:
        app = FastAPI()
        app.include_router(readiness.router)
        app.dependency_overrides[readiness.database_is_reachable] = lambda: database_reachable
        app.dependency_overrides[readiness.object_storage_is_reachable] = lambda: storage_reachable
        app.dependency_overrides[readiness.redis_is_reachable] = lambda: redis
        return TestClient(app)

    return build


def test_readiness_reports_ok_when_every_dependency_answers(client_factory) -> None:
    response = client_factory().get("/readiness")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "storage": "ok", "redis": "ok"},
    }


def test_readiness_reports_degraded_when_the_database_does_not_answer(
    client_factory,
) -> None:
    response = client_factory(database_reachable=False).get("/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": "unavailable", "storage": "ok", "redis": "ok"},
    }


def test_readiness_reports_degraded_when_storage_does_not_answer(client_factory) -> None:
    """Storage is a dependency of serving traffic, not an optional extra."""
    response = client_factory(storage_reachable=False).get("/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "storage": "unavailable", "redis": "ok"},
    }


def test_readiness_reports_degraded_when_redis_does_not_answer(client_factory) -> None:
    """A dead queue leaves analyses piling up in `uploaded` while the API reports `ok`
    — that is the outage #266 put this check here to name."""
    response = client_factory(redis="unavailable").get("/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "storage": "ok", "redis": "unavailable"},
    }


def test_readiness_stays_ok_when_redis_is_not_configured(client_factory) -> None:
    """Unset is allowed on the same terms as the limiter allows it: reported, not failed."""
    response = client_factory(redis="not_configured").get("/readiness")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["checks"]["redis"] == "not_configured"


class _FakeRedis:
    def __init__(self, *, fails: bool) -> None:
        self.fails = fails

    async def ping(self) -> bool:
        if self.fails:
            raise ConnectionError("refused")
        return True


def test_the_redis_check_reports_not_configured_when_the_url_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REDIS_URL_ENV_VAR, raising=False)

    assert asyncio.run(readiness.redis_is_reachable()) == "not_configured"


@pytest.mark.parametrize(("fails", "expected"), [(False, "ok"), (True, "unavailable")])
def test_the_redis_check_pings_the_configured_client(
    monkeypatch: pytest.MonkeyPatch, fails: bool, expected: str
) -> None:
    monkeypatch.setenv(REDIS_URL_ENV_VAR, "redis://:local@localhost:6379/0")
    fake: Any = _FakeRedis(fails=fails)
    monkeypatch.setattr(readiness, "get_redis", lambda: fake)

    assert asyncio.run(readiness.redis_is_reachable()) == expected


def test_readiness_is_tagged_health_in_the_openapi_schema(client_factory) -> None:
    schema = client_factory(True).app.openapi()

    assert schema["paths"]["/readiness"]["get"]["tags"] == ["health"]


def test_readiness_documents_both_outcomes_in_the_openapi_schema(client_factory) -> None:
    """apps/web generates its types from this schema — ADR 0001."""
    responses = client_factory(True).app.openapi()["paths"]["/readiness"]["get"]["responses"]

    assert set(responses) >= {"200", "503"}
    for status_code in ("200", "503"):
        schema_ref = responses[status_code]["content"]["application/json"]["schema"]
        assert "ReadinessResponse" in schema_ref["$ref"]
