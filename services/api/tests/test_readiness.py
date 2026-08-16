"""Unit tests for the readiness router.

The app under test is built locally rather than through `create_app()`: the
router must be usable on its own, and this keeps the test independent of the
application factory.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tcg_api.routers import readiness


@pytest.fixture
def client_factory():
    def build(database_reachable: bool) -> TestClient:
        app = FastAPI()
        app.include_router(readiness.router)
        app.dependency_overrides[readiness.database_is_reachable] = lambda: database_reachable
        return TestClient(app)

    return build


def test_readiness_reports_ok_when_the_database_answers(client_factory) -> None:
    response = client_factory(True).get("/readiness")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_readiness_reports_degraded_when_the_database_does_not_answer(
    client_factory,
) -> None:
    response = client_factory(False).get("/readiness")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"database": "unavailable"}}


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
