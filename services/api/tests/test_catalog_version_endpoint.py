"""`GET /catalog/version` — issue #27.

Spec §57 requires every analysis to record which card catalog it used, so a
deployment has to be able to say which one it is serving. These tests run
without PostgreSQL: the repository is a fake supplied through
`dependency_overrides`, which is what makes the two failure paths — the catalog
unreachable, and no version registered — testable at all. Neither is reachable
against a working database on purpose.

`test_catalog_versions.py` proves the real adapter answers the same questions
against real PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.routers.catalog import card_database_version_repository
from tcg_api.storage import get_object_storage
from tcg_domain.catalog_version import CardDatabaseVersion
from tcg_domain.errors import CatalogUnavailable

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

SEEDED = CardDatabaseVersion(
    version="pokemon-catalog-seed-v0.0.0",
    source="manual",
    generated_at=datetime(2026, 8, 18, tzinfo=UTC),
    set_count=4,
    card_count=22,
    external_id_count=23,
)

IMPORTED = CardDatabaseVersion(
    version="pokemon-catalog-v0.3.0",
    source="tcgdex",
    generated_at=datetime(2026, 8, 18, tzinfo=UTC),
    set_count=180,
    card_count=19_000,
    external_id_count=19_000,
    source_license="MIT",
    source_revision="8f2c1ab",
)


class StubRepository:
    """Answers `current()` with whatever the test asked for."""

    def __init__(self, current: CardDatabaseVersion | None = None) -> None:
        self._current = current

    async def current(self) -> CardDatabaseVersion | None:
        return self._current

    async def get(self, version: str) -> CardDatabaseVersion | None:
        return self._current if self._current and self._current.version == version else None


class UnreachableRepository:
    """The adapter's promise when the driver fails: `CatalogUnavailable`, never asyncpg."""

    async def current(self) -> CardDatabaseVersion | None:
        raise CatalogUnavailable("The card catalog could not be reached.")

    async def get(self, version: str) -> CardDatabaseVersion | None:
        raise CatalogUnavailable("The card catalog could not be reached.")


def app_with(repository: object) -> object:
    app = create_app()
    app.dependency_overrides[card_database_version_repository] = lambda: repository
    return app


# ---------------------------------------------------------------------------
# The endpoint is mounted, and reports the current version
# ---------------------------------------------------------------------------
def test_the_catalog_version_endpoint_is_mounted_on_the_application() -> None:
    """A route that exists on the router but not on the app is a silent 404."""
    with TestClient(app_with(StubRepository(SEEDED))) as client:
        assert client.get("/catalog/version").status_code != 404


def test_the_current_catalog_version_is_reported() -> None:
    with TestClient(app_with(StubRepository(SEEDED))) as client:
        response = client.get("/catalog/version")

    assert response.status_code == 200
    assert response.json() == {
        "version": "pokemon-catalog-seed-v0.0.0",
        "source": "manual",
        "source_license": None,
        "source_revision": None,
        "generated_at": "2026-08-18T00:00:00Z",
        "record_counts": {"sets": 4, "cards": 22, "external_ids": 23},
    }


def test_the_provenance_adr_0004_requires_reaches_the_client() -> None:
    """Licence and upstream revision are what make an import repeatable."""
    with TestClient(app_with(StubRepository(IMPORTED))) as client:
        body = client.get("/catalog/version").json()

    assert body["source"] == "tcgdex"
    assert body["source_license"] == "MIT"
    assert body["source_revision"] == "8f2c1ab"


def test_no_internal_key_is_published() -> None:
    """The surrogate id and the publication ordinal are ours, not a client's.

    Publishing the ordinal would invite a client to sort on an internal key.
    """
    with TestClient(app_with(StubRepository(SEEDED))) as client:
        body = client.get("/catalog/version").json()

    assert "id" not in body
    assert "ordinal" not in body


# ---------------------------------------------------------------------------
# The two failure paths, both inside spec §66's taxonomy
# ---------------------------------------------------------------------------
def test_an_unregistered_catalog_is_a_503_and_says_so() -> None:
    """Migrated but never seeded or imported. Nothing a retried request finds."""
    with TestClient(app_with(StubRepository(None))) as client:
        response = client.get("/catalog/version")

    assert response.status_code == 503
    assert response.json()["code"] == "provider_error"
    assert response.json()["details"] == {"reason": "no_catalog_version_registered"}


def test_an_unreachable_catalog_is_a_503_and_says_so_differently() -> None:
    with TestClient(app_with(UnreachableRepository())) as client:
        response = client.get("/catalog/version")

    assert response.status_code == 503
    assert response.json()["code"] == "provider_error"
    assert response.json()["details"] == {"reason": "catalog_unreachable"}


def test_no_ninth_error_code_was_invented() -> None:
    """Spec §66 names eight codes and a 404 would need a ninth.

    It would also be the wrong answer: nothing is missing that a different
    request would find — the deployment has no catalog at all.
    """
    from tcg_api.errors import ErrorCode

    with TestClient(app_with(StubRepository(None))) as client:
        code = client.get("/catalog/version").json()["code"]

    assert code in {member.value for member in ErrorCode}


def test_no_connection_detail_reaches_the_client() -> None:
    """Spec §66: "do not expose raw stack traces to users"."""
    with TestClient(app_with(UnreachableRepository())) as client:
        body = client.get("/catalog/version").text

    assert "postgresql" not in body.lower()
    assert "asyncpg" not in body.lower()


# ---------------------------------------------------------------------------
# `/health` stays dependency-free (the issue requires this explicitly)
# ---------------------------------------------------------------------------
@pytest.fixture
def app_without_a_database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TCG_API_DATABASE_URL", raising=False)
    for cached in CACHES:
        cached.cache_clear()
    yield create_app()
    for cached in CACHES:
        cached.cache_clear()


def test_the_catalog_version_is_not_reported_by_health(app_without_a_database) -> None:
    """The version is DB-backed; `/health` must answer while PostgreSQL is down.

    Asserted with no database configured at all, so a future change that read
    the catalog from the health handler fails here rather than passing every
    test in `test_health.py`.
    """
    with TestClient(app_without_a_database) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert set(response.json()) == {"status", "application_version"}


def test_the_catalog_version_endpoint_needs_a_database(app_without_a_database) -> None:
    """An unconfigured database is the same 503 as an unreachable one.

    A deployment with no `TCG_API_DATABASE_URL` cannot say which catalog it is
    serving either, and that is not a 500 — nothing unexpected happened.
    """
    with TestClient(app_without_a_database) as client:
        response = client.get("/catalog/version")

    assert response.status_code == 503
    assert response.json()["details"] == {"reason": "catalog_unreachable"}


# ---------------------------------------------------------------------------
# The contract apps/web generates from
# ---------------------------------------------------------------------------
def test_the_endpoint_is_documented_with_a_response_model() -> None:
    """A bare dict would leave `apps/web` without a generated type — ADR 0001."""
    schema = create_app().openapi()
    operation = schema["paths"]["/catalog/version"]["get"]

    reference = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    model = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]

    assert set(model["properties"]) == {
        "version",
        "source",
        "source_license",
        "source_revision",
        "generated_at",
        "record_counts",
    }


def test_the_unavailable_response_is_documented() -> None:
    """The 503 is the endpoint's own; the 500 comes from `ERROR_RESPONSES`."""
    operation = create_app().openapi()["paths"]["/catalog/version"]["get"]

    for status_code in ("503", "500"):
        reference = operation["responses"][status_code]["content"]["application/json"]["schema"][
            "$ref"
        ]
        assert reference.endswith("/ErrorResponse")
