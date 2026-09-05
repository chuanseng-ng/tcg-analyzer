"""`POST /analyses`, `GET /analyses/{id}` and `POST /analyses/{id}/run` — #32, #35.

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
from tcg_api.analysis import jobs
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.errors import ErrorCode
from tcg_api.routers.analyses import SESSION_COOKIE
from tcg_api.routers.cards import card_repository
from tcg_api.storage import get_object_storage
from tcg_api.version import application_version
from tcg_domain.errors import CatalogUnavailable

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

    `card_database_versions` is emptied for a second reason, on the precedent
    `test_catalog_versions.py` sets: spec §57's record names the *published*
    catalog, so these tests publish one — and the table's own trigger refuses
    `DELETE`, which makes TRUNCATE the only way not to leave a fabricated
    version in a developer's database forever.
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
                    sa.text(
                        "TRUNCATE analysis_sessions, card_database_versions "
                        "RESTART IDENTITY CASCADE"
                    )
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
# Running one — issue #35
#
# The queue itself is not exercised here; `test_analysis_jobs.py` owns that, and
# it needs no broker either. What these cover is the endpoint's own contract:
# who may run an analysis, when, and what the caller is told.
#
# Nothing reaches `uploaded` on its own yet — the upload endpoint is a later
# issue — so the state is written directly, exactly as the expiry tests above
# write an elapsed `expires_at`.
# ---------------------------------------------------------------------------


def uploaded(analysis_id: str) -> None:
    """Put an analysis where a run may start from."""
    executing("UPDATE analyses SET status = 'uploaded' WHERE id = :id", id=uuid.UUID(analysis_id))


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """What the endpoint handed to the queue, in place of a broker.

    Patched on the router's own name rather than on `jobs`, because that is the
    reference the endpoint actually calls — patching the definition would leave
    the imported alias pointing at the original.
    """
    handed: list[uuid.UUID] = []

    def record(analysis_id: uuid.UUID) -> str:
        handed.append(analysis_id)
        return "job-1"

    monkeypatch.setattr("tcg_api.routers.analyses.enqueue_analysis", record)
    return handed


def worked(analysis_id: str) -> None:
    """Run the job the way a worker would, without a worker.

    The task's function, with a request pushed so `self.request` is populated —
    the same seam `test_analysis_jobs.py` uses. No broker, no eager mode, and no
    `asyncio.run` nested inside the test client's event loop.
    """
    jobs.run_analysis.push_request(retries=0, id="job-1", called_directly=False)
    try:
        jobs.run_analysis.run(analysis_id)
    finally:
        jobs.run_analysis.pop_request()


@pytest.mark.integration
@requires_postgres
def test_running_an_analysis_answers_at_once(client: TestClient, enqueued: list[uuid.UUID]) -> None:
    """Spec §65's response, verbatim: `analysis_id` and `queued`."""
    created = client.post("/analyses").json()
    uploaded(created["id"])

    response = client.post(f"/analyses/{created['id']}/run")

    assert response.status_code == 202
    assert response.json() == {"analysis_id": created["id"], "status": "queued"}
    assert enqueued == [uuid.UUID(created["id"])]


@pytest.mark.integration
@requires_postgres
def test_queueing_does_not_move_the_analysis(client: TestClient, enqueued: list[uuid.UUID]) -> None:
    """`queued` is a transport word; no row ever holds it, and none is advanced by it.

    Marking the row here would mean a broker that swallowed the message left an
    analysis in a state nothing would move it out of. The worker's own claim is
    the only thing that advances one.
    """
    created = client.post("/analyses").json()
    uploaded(created["id"])

    client.post(f"/analyses/{created['id']}/run")

    assert client.get(f"/analyses/{created['id']}").json()["status"] == "uploaded"


@pytest.mark.integration
@requires_postgres
def test_an_analysis_with_no_images_is_not_run(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    """Spec §18's pipeline starts with images, so there is nothing to analyse."""
    created = client.post("/analyses").json()

    response = client.post(f"/analyses/{created['id']}/run")

    assert response.status_code == 409
    assert enqueued == []


@pytest.mark.integration
@requires_postgres
def test_another_sessions_analysis_cannot_be_run(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    """The same answer as reading it, so running is not a way to discover one exists."""
    created = client.post("/analyses").json()
    uploaded(created["id"])
    client.cookies.clear()
    client.post("/analyses")

    response = client.post(f"/analyses/{created['id']}/run")

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND
    assert enqueued == []


@pytest.mark.integration
@requires_postgres
def test_an_unknown_analysis_cannot_be_run(client: TestClient, enqueued: list[uuid.UUID]) -> None:
    client.post("/analyses")

    response = client.post(f"/analyses/{uuid.uuid4()}/run")

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND


@pytest.mark.integration
@requires_postgres
def test_running_a_malformed_identifier_is_a_validation_error(client: TestClient) -> None:
    client.post("/analyses")

    assert client.post("/analyses/not-a-uuid/run").status_code == 422


@pytest.mark.integration
@requires_postgres
def test_a_run_reaches_the_confirmation_gate_and_stops(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    """Where a run rests, and deliberately not `completed`.

    Spec §20 forbids acting on an identification the user has not confirmed, and
    nothing in this milestone produces a candidate — so the honest end of the
    worker's pipeline is the gate. Advancing further here would report a finished
    analysis whose economics nobody configured; `completed` is written by
    `POST /analyses/{id}/economic-configuration` (#244), not by the worker.
    """
    created = client.post("/analyses").json()
    uploaded(created["id"])
    client.post(f"/analyses/{created['id']}/run")

    worked(created["id"])

    polled = client.get(f"/analyses/{created['id']}").json()
    assert polled["status"] == "awaiting_confirmation"
    assert polled["completed_at"] is None
    assert polled["card_id"] is None


# ---------------------------------------------------------------------------
# Spec §57's reproducibility record — issue #40
#
# The record is captured by the *worker*, at the moment it claims the analysis,
# so these drive `worked()` rather than the endpoint. What the endpoint owes is
# the other half: reporting what was stored, without resolving anything itself.
# ---------------------------------------------------------------------------

CATALOG_VERSION = "pokemon-catalog-v9.9.9"


def publish_grading_rules() -> None:
    """Publish spec §23's three standards, the way `tcg-seed-grading-rules` does.

    The seeder's own writer, because what is being tested is that the run
    records whatever the table says is in force — and a fresh database says
    nothing until something publishes.
    """
    from tcg_api.grading.seed import apply_grading_rules, load_grading_rules

    async def publish() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            await apply_grading_rules(load_grading_rules(), engine)
        finally:
            await engine.dispose()

    run(publish)


def publish_catalog_version(version: str = CATALOG_VERSION) -> None:
    """Register a card database version, past the import pipeline.

    Straight SQL for the reason the run states are written straight: what is
    being tested is that the run captures whatever is published, not how a
    version comes to be published.
    """
    executing(
        "INSERT INTO card_database_versions "
        "(id, version, source, generated_at, set_count, card_count, external_id_count) "
        "VALUES (:id, :version, 'manual', now(), 0, 0, 0) "
        "ON CONFLICT (version) DO NOTHING",
        id=uuid.uuid5(uuid.NAMESPACE_URL, version),
        version=version,
    )


@pytest.mark.integration
@requires_postgres
def test_a_run_records_what_it_was_computed_against(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    """Spec §57, the whole record, on an analysis that has actually run.

    `application_version` is the running application's, resolved by the process
    that did the work; `card_database_version` is the published identifier that
    was current at that moment; `model_bundle_version` is the composed
    condition version joined to the composed grading version, both resolved at
    the claim (#187, #227); `grading_rules_version` is the three standards in
    force at that moment, in slug order (#227, ADR 0011). The two that name
    components no milestone has built yet are null, and null here means "does
    not exist", not "not sent".
    """
    # Imported here rather than at the top: the constants live beside the CV
    # stack and the `tcg_ml_` prefix, and only this test needs them.
    from tcg_api.analysis.grading import GRADING_VERSION
    from tcg_ml_condition import CONDITION_VERSION

    publish_catalog_version()
    publish_grading_rules()
    created = client.post("/analyses").json()
    uploaded(created["id"])
    client.post(f"/analyses/{created['id']}/run")

    worked(created["id"])

    record = client.get(f"/analyses/{created['id']}").json()["reproducibility"]
    assert record["application_version"] == application_version()
    # Read back rather than compared to the literal, so this says "the catalog
    # that was current" rather than "the string this test happened to publish".
    assert record["card_database_version"] == querying(
        "SELECT version FROM card_database_versions ORDER BY ordinal DESC LIMIT 1"
    )
    assert record["model_bundle_version"] == f"{CONDITION_VERSION}+{GRADING_VERSION}"
    # Read back the same way: the standards the table says are in force today,
    # not the strings the seed happened to publish.
    assert record["grading_rules_version"] == "+".join(
        querying(
            "SELECT version FROM grading_rules WHERE company = :company "
            "AND (effective_from IS NULL OR effective_from <= current_date) "
            "ORDER BY effective_from DESC NULLS LAST LIMIT 1",
            company=company,
        )
        for company in ("bgs", "psa", "tag")
    )
    assert record["market_snapshot_id"] is None
    assert record["economic_configuration_id"] is None
    # M8's acceptance criterion in the product (#227): a distribution per
    # company, kept in full, stored beside the condition it was read from.
    predictions = querying(
        "SELECT grade_predictions -> 'predictions' FROM analyses WHERE id = :id",
        id=uuid.UUID(created["id"]),
    )
    assert set(predictions) == {"bgs", "psa", "tag"}


@pytest.mark.integration
@requires_postgres
def test_an_analysis_that_has_not_run_carries_an_empty_record(client: TestClient) -> None:
    """Every field null, and the object present rather than omitted.

    The record is captured at execution time, so an analysis nothing has run has
    nothing to record — and says so, rather than reporting the versions that
    happen to be current when it is polled.
    """
    created = client.post("/analyses").json()

    record = client.get(f"/analyses/{created['id']}").json()["reproducibility"]

    assert record["application_version"] is None
    assert record["card_database_version"] is None
    assert record["image_sha256"] == {}


@pytest.mark.integration
@requires_postgres
def test_a_stored_record_does_not_follow_the_running_version(
    client: TestClient, enqueued: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deploying a new version must not rewrite what an old analysis recorded.

    This is the property the whole issue exists for: a record that tracked the
    current version would say the same thing as no record at all. Asserted by
    moving the version *after* the run and polling again — the answer must be
    the version that ran.
    """
    publish_catalog_version()
    created = client.post("/analyses").json()
    uploaded(created["id"])
    client.post(f"/analyses/{created['id']}/run")
    worked(created["id"])
    ran_as = client.get(f"/analyses/{created['id']}").json()["reproducibility"]

    monkeypatch.setattr("tcg_api.routers.analyses.application_version", lambda: "99.0.0")
    publish_catalog_version("pokemon-catalog-v9.9.10")

    polled = client.get(f"/analyses/{created['id']}").json()["reproducibility"]
    assert polled == ran_as
    assert polled["card_database_version"] != "pokemon-catalog-v9.9.10"


@pytest.mark.integration
@requires_postgres
def test_the_recorded_hashes_are_the_hashes_of_the_stored_images(client: TestClient) -> None:
    """§57's input image hashes, and the same digests the upload answered with.

    Read from `images.sha256`, which is a digest of the bytes that were *stored*
    rather than the bytes that arrived — the distinction #33 made so that a
    cache key names bytes somebody kept.
    """
    created = client.post("/analyses").json()
    digests = {"front": "a" * 64, "back": "b" * 64}
    for side, digest in digests.items():
        # Written straight, as the run states above are: what is under test is
        # that the response reports `images.sha256`, not how a row gets there.
        executing(
            "INSERT INTO images (id, analysis_id, side, original_uri, mime_type, sha256) "
            "VALUES (:id, :analysis_id, :side, :uri, 'image/jpeg', :sha256)",
            id=uuid.uuid4(),
            analysis_id=uuid.UUID(created["id"]),
            side=side,
            uri=f"uploads/{created['id']}/{side}.jpg",
            sha256=digest,
        )

    record = client.get(f"/analyses/{created['id']}").json()["reproducibility"]

    assert record["image_sha256"] == digests


@pytest.mark.integration
@requires_postgres
def test_an_analysis_already_running_is_not_queued_again(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    """At-least-once delivery is safe; asking for it twice is still a conflict."""
    created = client.post("/analyses").json()
    uploaded(created["id"])
    client.post(f"/analyses/{created['id']}/run")
    worked(created["id"])

    response = client.post(f"/analyses/{created['id']}/run")

    assert response.status_code == 409
    assert enqueued == [uuid.UUID(created["id"])]


@pytest.mark.integration
@requires_postgres
def test_a_duplicate_delivery_does_not_re_run_the_analysis(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    """The other half: two deliveries of one job leave the analysis where one did."""
    created = client.post("/analyses").json()
    uploaded(created["id"])
    client.post(f"/analyses/{created['id']}/run")

    worked(created["id"])
    worked(created["id"])

    assert client.get(f"/analyses/{created['id']}").json()["status"] == "awaiting_confirmation"


@pytest.mark.integration
@requires_postgres
def test_an_unreachable_queue_is_a_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker that is down is `provider_error`, with its own reason.

    Distinct from the store's, because an operator reading the log should not
    have to work out which of the two dependencies is missing.
    """

    def refuse(analysis_id: uuid.UUID) -> str:
        raise jobs.JobQueueUnavailable("nope")

    monkeypatch.setattr("tcg_api.routers.analyses.enqueue_analysis", refuse)
    created = client.post("/analyses").json()
    uploaded(created["id"])

    response = client.post(f"/analyses/{created['id']}/run")

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == ErrorCode.PROVIDER_ERROR.value
    assert body["details"]["reason"] == "job_queue_unreachable"


# ---------------------------------------------------------------------------
# Confirming the card — issue #104
#
# The card is real, because the foreign key is: `analyses.card_id` references
# `cards.id` with `RESTRICT`, so a fake repository saying "yes" to an identifier
# no row carries would be testing the fake and then failing on the constraint.
# One set and one card are inserted for the module, and the real
# `PostgresCardRepository` resolves them.
#
# The state is written directly, exactly as `uploaded()` above does. `worked()`
# would also reach it, but through a worker this endpoint's contract does not
# depend on.
# ---------------------------------------------------------------------------

CARD_ID = uuid.UUID("11111111-1111-5111-8111-111111111111")
SET_ID = uuid.UUID("22222222-2222-5222-8222-222222222222")


@pytest.fixture(scope="module", autouse=True)
def catalogued(migrated: None) -> None:
    """One card for confirmations to name, inserted past the API.

    Takes `migrated` rather than trusting definition order: two autouse fixtures
    of the same scope are not ordered by where they appear, and this one writes
    to a table that one creates.

    Idempotent, because the module fixture that empties the session tree does
    not touch the catalog and a developer may run this file twice.
    """
    if not DATABASE_URL:
        return

    executing(
        "INSERT INTO sets (id, game, language, set_code, name) "
        "VALUES (:id, 'pokemon', 'en', 'CONF', 'Confirmation Test Set') "
        "ON CONFLICT (id) DO NOTHING",
        id=SET_ID,
    )
    executing(
        "INSERT INTO cards (id, game, language, set_id, card_number, name, variant) "
        "VALUES (:id, 'pokemon', 'en', :set_id, '1/1', 'Confirmation Test Card', 'holo') "
        "ON CONFLICT (id) DO NOTHING",
        id=CARD_ID,
        set_id=SET_ID,
    )


def confirmable(analysis_id: str) -> None:
    """Put an analysis where a confirmation may be recorded from."""
    executing(
        "UPDATE analyses SET status = 'awaiting_confirmation' WHERE id = :id",
        id=uuid.UUID(analysis_id),
    )


def at_the_gate(client: TestClient) -> str:
    """An analysis of this client's, waiting for its card."""
    created = client.post("/analyses").json()
    confirmable(created["id"])
    return str(created["id"])


@pytest.mark.integration
@requires_postgres
def test_confirming_records_the_card_and_moves_the_analysis_on(client: TestClient) -> None:
    """The acceptance criterion: the analysis records which card it is."""
    analysis_id = at_the_gate(client)

    response = client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)})

    assert response.status_code == 200
    body = response.json()
    assert body["card_id"] == str(CARD_ID)
    assert body["status"] == "analyzing"
    # Past the API, because the response could agree with itself while the row
    # said something else.
    assert querying("SELECT card_id FROM analyses WHERE id = :id", id=uuid.UUID(analysis_id)) == (
        CARD_ID
    )
    assert (
        querying("SELECT status FROM analyses WHERE id = :id", id=uuid.UUID(analysis_id))
        == "analyzing"
    )


@pytest.mark.integration
@requires_postgres
def test_a_confirmed_analysis_reports_its_card_when_polled(client: TestClient) -> None:
    """§65's poll is where a client learns this, so it has to carry it."""
    analysis_id = at_the_gate(client)
    client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)})

    polled = client.get(f"/analyses/{analysis_id}").json()

    assert polled["card_id"] == str(CARD_ID)
    assert polled["status"] == "analyzing"
    assert polled["completed_at"] is None


@pytest.mark.integration
@requires_postgres
@pytest.mark.parametrize("state", ["created", "uploading", "uploaded", "analyzing", "completed"])
def test_only_an_analysis_waiting_for_a_card_takes_one(client: TestClient, state: str) -> None:
    """Spec §20 makes confirmation a step, not something available at any moment.

    `completed` is what the economic configuration leaves behind (#244), and
    `awaiting_confirmation` is the only state a confirmation may be recorded
    from — so a finished analysis refuses one with no new code.
    """
    created = client.post("/analyses").json()
    executing(
        "UPDATE analyses SET status = :state, completed_at = "
        "CASE WHEN :state IN ('completed', 'failed') THEN now() END WHERE id = :id",
        state=state,
        id=uuid.UUID(created["id"]),
    )

    response = client.post(
        f"/analyses/{created['id']}/confirm-card", json={"card_id": str(CARD_ID)}
    )

    assert response.status_code == 409
    assert state in response.json()["detail"]
    assert querying("SELECT card_id FROM analyses WHERE id = :id", id=uuid.UUID(created["id"])) is (
        None
    )


@pytest.mark.integration
@requires_postgres
def test_a_failed_analysis_says_so_rather_than_asking_for_another_try(
    client: TestClient,
) -> None:
    """`failed` is a 409 like the rest, and used to read like the rest — "not
    ready for this yet", which invites a retry that spec §65 makes impossible.

    It carries the §66 envelope instead. `analysis_failed` is the general case:
    a job that ran out of retries, or a dependency that never came back.
    """
    created = client.post("/analyses").json()
    executing("UPDATE analyses SET status = 'failed' WHERE id = :id", id=uuid.UUID(created["id"]))

    response = client.post(
        f"/analyses/{created['id']}/confirm-card", json={"card_id": str(CARD_ID)}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "analysis_failed"


@pytest.mark.integration
@requires_postgres
def test_a_quality_failure_is_named_as_one(client: TestClient) -> None:
    """The gate refusing the photographs is the one failure the user can act on,
    so it gets spec §66's code for exactly that and names the sides."""
    created = client.post("/analyses").json()
    analysis_id = uuid.UUID(created["id"])
    executing(
        "INSERT INTO images (id, analysis_id, side, original_uri, mime_type, sha256, "
        "quality_score, quality_status) VALUES "
        "(:image_id, :id, 'front', 'uploads/front', 'image/jpeg', :digest, 0.05, 'unusable')",
        image_id=uuid.uuid4(),
        id=analysis_id,
        digest="a" * 64,
    )
    executing("UPDATE analyses SET status = 'failed' WHERE id = :id", id=analysis_id)

    response = client.post(
        f"/analyses/{created['id']}/confirm-card", json={"card_id": str(CARD_ID)}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "image_quality_failure"
    assert response.json()["details"]["sides"] == ["front"]


@pytest.mark.integration
@requires_postgres
def test_confirming_twice_is_refused(client: TestClient) -> None:
    """Decided rather than left open: §65 moves forwards only, so there is no
    second confirmation and no changing the card afterwards. Starting over is
    how a mistake is corrected."""
    analysis_id = at_the_gate(client)
    first = client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)})

    second = client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)})

    assert first.status_code == 200
    assert second.status_code == 409
    assert querying("SELECT card_id FROM analyses WHERE id = :id", id=uuid.UUID(analysis_id)) == (
        CARD_ID
    )


@pytest.mark.integration
@requires_postgres
def test_a_card_the_catalog_does_not_hold_is_refused(client: TestClient) -> None:
    """Spec §55: the identifier comes from a client and is not trusted.

    The same `card_not_identified` `GET /cards/{id}` answers with, so "no card
    is recorded under that identifier" means one thing across the product.
    """
    analysis_id = at_the_gate(client)

    response = client.post(
        f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(uuid.uuid4())}
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == ErrorCode.CARD_NOT_IDENTIFIED.value
    # Nothing was written, and the analysis is still confirmable.
    assert (
        querying("SELECT status FROM analyses WHERE id = :id", id=uuid.UUID(analysis_id))
        == "awaiting_confirmation"
    )


@pytest.mark.integration
@requires_postgres
def test_another_sessions_analysis_cannot_be_confirmed(client: TestClient) -> None:
    """The same 404, with the same body, for the same reason as the read."""
    analysis_id = at_the_gate(client)
    client.cookies.clear()
    client.post("/analyses")

    response = client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)})

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND
    assert querying("SELECT card_id FROM analyses WHERE id = :id", id=uuid.UUID(analysis_id)) is (
        None
    )


@pytest.mark.integration
@requires_postgres
def test_confirming_without_a_session_is_the_same_404(client: TestClient) -> None:
    analysis_id = at_the_gate(client)
    client.cookies.clear()

    response = client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)})

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND


@pytest.mark.integration
@requires_postgres
def test_an_unreachable_catalog_is_a_503_naming_the_catalog(client: TestClient) -> None:
    """A fourth `details.reason`, because a confirmation reads a fourth dependency.

    An operator reading a 503 from this endpoint should be told whether it is
    the analysis store or the catalog that is not answering, rather than
    guessing between them.
    """
    analysis_id = at_the_gate(client)

    class Refusing:
        """`get` is what raises, exactly as `PostgresCardRepository`'s does.

        A dependency that raised instead would be testing FastAPI's resolution
        rather than the route's `except` clause.
        """

        async def get(self, card_id: object) -> None:
            raise CatalogUnavailable("nope")

    client.app.dependency_overrides[card_repository] = Refusing
    try:
        response = client.post(
            f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)}
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == ErrorCode.PROVIDER_ERROR.value
    assert body["details"]["reason"] == "catalog_unreachable"


@pytest.mark.integration
@requires_postgres
def test_a_malformed_card_id_is_a_validation_error(client: TestClient) -> None:
    """FastAPI's own 422, on the same terms a malformed path identifier gets."""
    analysis_id = at_the_gate(client)

    response = client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": "not-a-uuid"})

    assert response.status_code == 422


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


@pytest.mark.integration
@requires_postgres
def test_a_worked_analysis_serves_its_grade_distributions(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    """M8 end to end (#227, #228): what the worker stored is what the results serve.

    The real predictors, the real route; no snapshot, because none exists on a
    deployment that has never ingested (ADR 0006). With nothing uploaded the
    gate lets nothing through to the analyzers, so every company's stored
    entry is a refusal — and a refusal is served as one, never fabricated into
    a distribution.
    """
    publish_grading_rules()
    created = client.post("/analyses").json()
    uploaded(created["id"])
    client.post(f"/analyses/{created['id']}/run")
    worked(created["id"])

    status = client.get(f"/analyses/{created['id']}").json()["status"]
    assert status == "awaiting_confirmation", status
    assert (
        client.post(
            f"/analyses/{created['id']}/confirm-card", json={"card_id": str(CARD_ID)}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/analyses/{created['id']}/economic-configuration",
            json={"grading_companies": ["psa", "bgs"], "optimization_mode": "expected_profit"},
        ).status_code
        == 201
    )

    body = client.get(f"/analyses/{created['id']}/results").json()

    stored = querying(
        "SELECT grade_predictions -> 'predictions' FROM analyses WHERE id = :id",
        id=uuid.UUID(created["id"]),
    )
    assert set(stored) == {"bgs", "psa", "tag"}
    served = {company["company"]: company for company in body["companies"]}
    for slug in ("psa", "bgs"):
        entry = stored[slug]
        if "distribution" in entry:
            assert {
                term["grade"]: term["probability"] for term in served[slug]["grade_distribution"]
            } == entry["distribution"]
        else:
            assert slug not in served
    # Nothing was uploaded, so nothing was assessed: every entry is a refusal
    # and there is no photograph for §44's third confidence source — the
    # route answers `null`, "not asked", rather than an admission nothing
    # measured.
    assert all("insufficient_information" in entry for entry in stored.values())
    assert body["companies"] == []
    assert body["recommendation"] is None
