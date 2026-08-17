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
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.routers.readiness import database_is_reachable, object_storage_is_reachable
from tcg_api.storage import get_object_storage

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)


@pytest.fixture
def app_without_dependencies(monkeypatch: pytest.MonkeyPatch):
    """The application with no database and no storage configuration at all."""
    for variable in ("TCG_API_DATABASE_URL", "TCG_API_STORAGE_BUCKET"):
        monkeypatch.delenv(variable, raising=False)
    for cached in CACHES:
        cached.cache_clear()
    yield create_app()
    for cached in CACHES:
        cached.cache_clear()


def app_with_dependencies(*, database: bool = True, storage: bool = True):
    """The application with both probes forced to a known answer."""
    app = create_app()
    app.dependency_overrides[database_is_reachable] = lambda: database
    app.dependency_overrides[object_storage_is_reachable] = lambda: storage
    return app


def test_readiness_is_mounted_on_the_application() -> None:
    """The route exists on the real app, not merely on the router.

    Asserted by making a request rather than by inspecting `app.routes`:
    FastAPI keeps included routers as opaque objects that expose no `path`, so
    introspection would test the framework's internals. A response that is not
    404 is the claim itself.
    """
    with TestClient(app_with_dependencies()) as client:
        assert client.get("/readiness").status_code != 404


def test_readiness_reports_ok_when_every_dependency_answers() -> None:
    with TestClient(app_with_dependencies()) as client:
        response = client.get("/readiness")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "storage": "ok"}}


@pytest.mark.parametrize("failing", ["database", "storage"])
def test_readiness_degrades_to_503_when_any_one_dependency_is_down(failing: str) -> None:
    """Either dependency alone is enough to make the service unready."""
    app = app_with_dependencies(database=failing != "database", storage=failing != "storage")

    with TestClient(app) as client:
        response = client.get("/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"][failing] == "unavailable"


def test_readiness_names_every_failing_dependency_not_merely_the_first() -> None:
    """An operator fixing a deployment wants the whole list in one round trip."""
    app = app_with_dependencies(database=False, storage=False)

    with TestClient(app) as client:
        response = client.get("/readiness")

    assert response.json() == {
        "status": "degraded",
        "checks": {"database": "unavailable", "storage": "unavailable"},
    }


def test_health_still_answers_without_any_database_configuration(app_without_dependencies) -> None:
    """The liveness probe must not have acquired a database dependency."""
    with TestClient(app_without_dependencies) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["application_version"]


@pytest.mark.parametrize("dependency", ["database", "storage"])
def test_readiness_is_degraded_rather_than_broken_without_configuration(
    app_without_dependencies, dependency: str
) -> None:
    """Unconfigured is an unready service, not a crashing one.

    Neither a missing `TCG_API_DATABASE_URL` nor a missing `TCG_API_STORAGE_*`
    may surface as a 500: misconfiguration is the likeliest reason a fresh
    deployment is not ready, so it is the last thing that should read as a bug
    in the probe.
    """
    with TestClient(app_without_dependencies) as client:
        response = client.get("/readiness")

    assert response.status_code == 503
    assert response.json()["checks"][dependency] == "unavailable"


def test_shutdown_does_not_build_an_engine_it_never_needed(app_without_dependencies) -> None:
    """A process that only served `/health` must not construct a pool to close one."""
    get_engine.cache_clear()

    with TestClient(app_without_dependencies) as client:
        client.get("/health")

    assert get_engine.cache_info().currsize == 0


def test_an_unhandled_exception_on_the_real_app_is_shaped(caplog) -> None:
    """The handlers are installed by `create_app`, not merely available.

    Exercised through a route added after construction, because no real
    endpoint should ever be written to fail on purpose.
    """
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise LookupError("internal detail that must not be published")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "LookupError" not in response.text


def test_both_probes_are_documented_in_the_openapi_schema() -> None:
    """apps/web generates its types from this schema — ADR 0001."""
    schema = create_app().openapi()

    assert "/health" in schema["paths"]
    assert "/readiness" in schema["paths"]
    assert "503" in schema["paths"]["/readiness"]["get"]["responses"]
