"""`POST /analyses` and `GET /analyses/{id}` — issue #32.

These run against real PostgreSQL rather than against a fake, and deliberately
so. What this endpoint pair has to get right is not a response shape — it is
that one anonymous user cannot read another's analysis, and that an expired
session stops working. Both of those live in a `WHERE` clause, and a stub that
answered them in Python would be testing the stub.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg

Every test here carries `integration` as well as the skip, on
`test_catalog_cards.py`'s pattern: the marker is what selects it in CI's
database job. The last test in the file is the exception, and carries neither —
it needs the database to be *unreachable*, which a working one cannot
demonstrate, so it belongs in the job that has no PostgreSQL at all.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.errors import ErrorCode
from tcg_api.routers.analyses import SESSION_COOKIE
from tcg_api.storage import get_object_storage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

#: Every process-wide cache that would otherwise carry one test's environment
#: into the next. `get_engine` and `get_session_factory` are not in `conftest.py`'s
#: list, because most tests never build one.
CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

#: The one message every "there is no such analysis for you" answer carries. A
#: caller must not be able to tell an unknown identifier from someone else's.
NOT_FOUND = "No analysis is recorded under that identifier."


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """Bring the schema up and empty the session tree once for the module.

    `test_migrations.py` leaves the database at `base` and pytest promises no
    ordering between files, so the migration cannot be assumed. The TRUNCATE is
    for the same reason `test_catalog_cards.py` truncates: these tests count
    rows, and a developer's database may hold sessions from a manual run.
    """
    if not DATABASE_URL:
        return

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    async def empty() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text("TRUNCATE analysis_sessions RESTART IDENTITY CASCADE")
                )
        finally:
            await engine.dispose()

    run(empty)


def querying(statement: str, **parameters: Any) -> Any:
    """Read one value straight out of PostgreSQL, past the API."""

    async def read() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                result = await connection.execute(sa.text(statement), parameters)
                return result.scalar()
        finally:
            await engine.dispose()

    return run(read)


def executing(statement: str, **parameters: Any) -> None:
    async def write() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text(statement), parameters)
        finally:
            await engine.dispose()

    run(write)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """One anonymous user, with their own cookie jar.

    Two details here are not decoration:

    * **`with`**, so every request in a test runs on one event loop. Without it
      `TestClient` opens a portal per request, and asyncpg's pooled connections
      belong to the loop that opened them.
    * **The engine caches are cleared first**, because `get_engine` is
      process-wide and would otherwise hand this client a pool bound to the
      previous test's loop. The application's own lifespan disposes it on the
      way out, which is why nothing is left to leak.

    Together they are also why a "second user" below is this same client with
    its cookies cleared rather than a second `TestClient`: one loop per test.
    """
    for cached in CACHES:
        cached.cache_clear()
    with TestClient(create_app()) as instance:
        yield instance


# ---------------------------------------------------------------------------
# Starting an analysis
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
def test_starting_an_analysis_needs_no_login(client: TestClient) -> None:
    """Spec §53's acceptance criterion, as one request."""
    response = client.post("/analyses")

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["status"] == "created"
    assert body["card_id"] is None
    assert body["completed_at"] is None


@pytest.mark.integration
@requires_postgres
def test_the_session_cookie_is_not_readable_by_script(client: TestClient) -> None:
    """HttpOnly is the whole point of carrying it in a cookie (issue #32).

    The token is the only thing separating one anonymous user's photographs
    from another's, so an XSS on `apps/web` must not be able to read it.
    """
    response = client.post("/analyses")

    header = response.headers["set-cookie"]
    assert header.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.replace("samesite", "SameSite")


@pytest.mark.integration
@requires_postgres
def test_session_tokens_are_long_and_unique(client: TestClient) -> None:
    """Unguessable, not merely unique: a sequential id would expose every session."""
    tokens = set()
    for _ in range(5):
        client.cookies.clear()
        client.post("/analyses")
        tokens.add(client.cookies[SESSION_COOKIE])

    assert len(tokens) == 5
    assert all(len(token) >= 32 for token in tokens)


@pytest.mark.integration
@requires_postgres
def test_a_second_analysis_reuses_the_same_session(client: TestClient) -> None:
    """ "Creating a session (if needed)" — the cookie is the "if needed"."""
    first = client.post("/analyses")
    token = client.cookies[SESSION_COOKIE]
    second = client.post("/analyses")

    assert first.json()["id"] != second.json()["id"]
    assert client.cookies[SESSION_COOKIE] == token
    assert (
        querying(
            "SELECT count(*) FROM analyses a JOIN analysis_sessions s ON s.id = a.session_id "
            "WHERE s.anonymous_session_id = :token",
            token=token,
        )
        == 2
    )


@pytest.mark.integration
@requires_postgres
def test_a_session_records_the_application_version(client: TestClient) -> None:
    """Spec §12 gives the column, and §57 wants the version that actually ran."""
    client.post("/analyses")

    recorded = querying(
        "SELECT application_version FROM analysis_sessions WHERE anonymous_session_id = :token",
        token=client.cookies[SESSION_COOKIE],
    )
    assert recorded


@pytest.mark.integration
@requires_postgres
def test_a_session_expires(client: TestClient) -> None:
    """Spec §54: expiry is the default, and the column is where the policy lands."""
    client.post("/analyses")

    lifetime = querying(
        "SELECT expires_at - created_at FROM analysis_sessions WHERE anonymous_session_id = :token",
        token=client.cookies[SESSION_COOKIE],
    )
    assert lifetime.total_seconds() == pytest.approx(get_settings().session_ttl_seconds, abs=5)


# ---------------------------------------------------------------------------
# Reading one back
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
def test_an_analysis_is_readable_by_the_session_that_started_it(client: TestClient) -> None:
    created = client.post("/analyses").json()

    response = client.get(f"/analyses/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.integration
@requires_postgres
def test_another_sessions_analysis_is_not_readable(client: TestClient) -> None:
    """The property the whole issue exists for."""
    created = client.post("/analyses").json()
    client.cookies.clear()
    client.post("/analyses")

    response = client.get(f"/analyses/{created['id']}")

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND


@pytest.mark.integration
@requires_postgres
def test_an_analysis_is_not_readable_without_a_session(client: TestClient) -> None:
    created = client.post("/analyses").json()
    client.cookies.clear()

    response = client.get(f"/analyses/{created['id']}")

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND


@pytest.mark.integration
@requires_postgres
def test_an_unknown_analysis_answers_exactly_as_a_forbidden_one(client: TestClient) -> None:
    """Two different questions, one answer, on purpose.

    A caller who could tell "no such analysis" from "not yours" could use this
    endpoint to enumerate which analyses exist.
    """
    created = client.post("/analyses").json()
    client.cookies.clear()
    client.post("/analyses")

    forbidden = client.get(f"/analyses/{created['id']}")
    unknown = client.get(f"/analyses/{uuid.uuid4()}")

    assert forbidden.status_code == unknown.status_code == 404
    assert forbidden.json() == unknown.json()


@pytest.mark.integration
@requires_postgres
def test_a_malformed_identifier_is_a_validation_error(client: TestClient) -> None:
    """FastAPI's own 422, as `GET /cards/{id}` decided for the same case (#29)."""
    client.post("/analyses")

    assert client.get("/analyses/not-a-uuid").status_code == 422


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
def test_an_expired_session_cannot_read_its_own_analysis(client: TestClient) -> None:
    created = client.post("/analyses").json()
    executing(
        "UPDATE analysis_sessions "
        "SET created_at = now() - interval '2 days', expires_at = now() - interval '1 day' "
        "WHERE anonymous_session_id = :token",
        token=client.cookies[SESSION_COOKIE],
    )

    response = client.get(f"/analyses/{created['id']}")

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND


@pytest.mark.integration
@requires_postgres
def test_an_expired_session_is_replaced_rather_than_reused(client: TestClient) -> None:
    """A lapsed token starts a fresh session; it never resurrects the old one."""
    client.post("/analyses")
    expired = client.cookies[SESSION_COOKIE]
    executing(
        "UPDATE analysis_sessions "
        "SET created_at = now() - interval '2 days', expires_at = now() - interval '1 day' "
        "WHERE anonymous_session_id = :token",
        token=expired,
    )

    client.post("/analyses")

    assert client.cookies[SESSION_COOKIE] != expired


@pytest.mark.integration
@requires_postgres
def test_an_unknown_token_starts_a_new_session(client: TestClient) -> None:
    """A forged or stale cookie is not an error; it is simply not a session."""
    forged = "not-a-token-this-service-ever-issued"

    # Sent as a header rather than through the jar, so the response's own
    # `Set-Cookie` can be read without httpx having merged the two.
    response = client.post("/analyses", headers={"Cookie": f"{SESSION_COOKIE}={forged}"})

    assert response.status_code == 201
    assert forged not in response.headers["set-cookie"]


# ---------------------------------------------------------------------------
# The database is down
# ---------------------------------------------------------------------------


def test_an_unreachable_database_is_a_503_rather_than_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No asyncpg exception escapes as an `internal_error`.

    Runs without the `integration` marker because it needs the database to be
    *absent*: a refused connection never becomes a `SQLAlchemyError` at all, so
    this is the path that proves the `OSError` clause in `analysis/sessions.py`
    is doing something.
    """
    monkeypatch.setenv("TCG_API_DATABASE_URL", "postgresql+asyncpg://tcg:tcg@127.0.0.1:1/tcg")
    for cached in CACHES:
        cached.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.post("/analyses")
    finally:
        for cached in CACHES:
            cached.cache_clear()

    assert response.status_code == 503
    assert response.json()["code"] == ErrorCode.PROVIDER_ERROR.value
