"""`/readiness` is mounted on the real application, and `/health` still is not.

`test_readiness.py` exercises the router on a throwaway `FastAPI()` instance, so
it cannot see whether `create_app` actually mounts it. That gap is exactly where
a route silently goes missing, so it is closed here.

The second assertion is the one that matters longer term: `/health` must remain
dependency-free (spec §57's `application_version` has to be readable from a
liveness probe even when PostgreSQL is unreachable). A future change that made
the health handler consult the database would still pass every test in
`test_health.py`, because those run with a working environment. This test fails
instead, because it removes the database entirely.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tcg_api.app import create_app
from tcg_api.database import get_database_settings, get_engine, get_session_factory
from tcg_api.routers.readiness import database_is_reachable


@pytest.fixture
def app_without_database(monkeypatch: pytest.MonkeyPatch):
    """The application with no database configuration at all."""
    monkeypatch.delenv("TCG_API_DATABASE_URL", raising=False)
    for cached in (get_database_settings, get_engine, get_session_factory):
        cached.cache_clear()
    yield create_app()
    for cached in (get_database_settings, get_engine, get_session_factory):
        cached.cache_clear()


def test_readiness_is_mounted_on_the_application() -> None:
    """The route exists on the real app, not merely on the router.

    Asserted by making a request rather than by inspecting `app.routes`:
    FastAPI keeps included routers as opaque objects that expose no `path`, so
    introspection would test the framework's internals. A response that is not
    404 is the claim itself.
    """
    app = create_app()
    app.dependency_overrides[database_is_reachable] = lambda: True

    with TestClient(app) as client:
        assert client.get("/readiness").status_code != 404


def test_readiness_reports_ok_when_the_database_answers() -> None:
    app = create_app()
    app.dependency_overrides[database_is_reachable] = lambda: True

    with TestClient(app) as client:
        response = client.get("/readiness")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_readiness_degrades_to_503_on_the_application() -> None:
    app = create_app()
    app.dependency_overrides[database_is_reachable] = lambda: False

    with TestClient(app) as client:
        response = client.get("/readiness")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"database": "unavailable"}}


def test_health_still_answers_without_any_database_configuration(app_without_database) -> None:
    """The liveness probe must not have acquired a database dependency."""
    with TestClient(app_without_database) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["application_version"]


def test_readiness_is_degraded_rather_than_broken_without_configuration(
    app_without_database,
) -> None:
    """No `TCG_API_DATABASE_URL` is an unready service, not a crashing one."""
    with TestClient(app_without_database) as client:
        response = client.get("/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


def test_shutdown_does_not_build_an_engine_it_never_needed(app_without_database) -> None:
    """A process that only served `/health` must not construct a pool to close one."""
    get_engine.cache_clear()

    with TestClient(app_without_database) as client:
        client.get("/health")

    assert get_engine.cache_info().currsize == 0


def test_both_probes_are_documented_in_the_openapi_schema() -> None:
    """apps/web generates its types from this schema — ADR 0001."""
    schema = create_app().openapi()

    assert "/health" in schema["paths"]
    assert "/readiness" in schema["paths"]
    assert "503" in schema["paths"]["/readiness"]["get"]["responses"]
