"""Spec §68's three routes — #270.

Against real PostgreSQL, on `test_economic_configuration_endpoint.py`'s terms
and for its reasons: what these endpoints have to get right is that one
anonymous user cannot mint over another's analysis, that a code is minted once
and spent once, and that every way of missing looks the same from outside. All
of those live in the database, and a stub answering them in Python would be
testing the stub.

The acceptance criterion is the first test in this file: a completed analysis
becomes a return code once, and that code accepts one grade.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis.tables import analyses
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.storage import get_object_storage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
    ),
]

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

SET_ID = uuid.UUID("3a0f1a1e-0000-4000-8000-000000000001")
CARD_ID = uuid.UUID("3a0f1a1e-0000-4000-8000-000000000002")

#: What #227 stores. Copied whole at mint, so the version travels with it.
PREDICTIONS: dict[str, Any] = {
    "version": "grading-psa-heuristic-v0.1.0",
    "thresholds": {"grading_psa_base_sigma": 0.6},
    "predictions": {
        "psa": {
            "distribution": {"9": 0.6, "10": 0.4},
            "model_confidence": 0.35,
            "model_version": "grading-psa-heuristic-v0.1.0",
        }
    },
}

ANSWER = {"grading_company": "psa", "grade": "9", "certification_number": "12345678"}


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


def executing(statement: str, **parameters: Any) -> None:
    async def write() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text(statement), parameters)
        finally:
            await engine.dispose()

    run(write)


def querying(statement: str, **parameters: Any) -> Any:
    async def read() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return (await connection.execute(sa.text(statement), parameters)).scalar()
        finally:
            await engine.dispose()

    return run(read)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
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


@pytest.fixture(autouse=True)
def catalog_row() -> Iterator[None]:
    """One printed card for the analyses to confirm.

    Seeded and deleted rather than truncated: `cards` is shared, and #196's
    guard exists because a fixture that truncates is one that can lose a corpus.
    `grade_feedback` goes first — it holds the card by RESTRICT.
    """
    _clear()
    executing(
        "INSERT INTO sets (id, game, language, set_code, name) "
        "VALUES (:id, 'pokemon', 'en', 'feedback-endpoint', 'Feedback Endpoint')",
        id=SET_ID,
    )
    executing(
        "INSERT INTO cards (id, game, language, set_id, card_number, name) "
        "VALUES (:id, 'pokemon', 'en', :set_id, '1/1', 'Feedback Endpoint')",
        id=CARD_ID,
        set_id=SET_ID,
    )
    yield
    _clear()


def _clear() -> None:
    executing("DELETE FROM grade_feedback WHERE card_id = :id", id=CARD_ID)
    executing("TRUNCATE analysis_sessions, economic_configurations CASCADE")
    executing("DELETE FROM cards WHERE id = :id", id=CARD_ID)
    executing("DELETE FROM sets WHERE id = :id", id=SET_ID)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """One anonymous user, with their own cookie jar."""
    for cached in CACHES:
        cached.cache_clear()
    with TestClient(create_app()) as instance:
        yield instance


@contextmanager
def another_visitor() -> Iterator[TestClient]:
    """A browser that has never seen this service — no cookie, no session.

    The caches are cleared on the way in so this application builds its own
    engine: an engine's pooled connections belong to the event loop that opened
    them and `TestClient` runs its own, so two clients sharing one cached engine
    is a `got Future attached to a different loop` waiting to happen.

    **And cleared again on the way out**, which is the half that is easy to
    miss. `lifespan` disposes `get_engine()` on shutdown when the cache holds
    one, so an outer client closing after this one would otherwise dispose
    *this* application's engine from a portal that has already stopped. The
    lifespan docstring anticipates exactly this and does the same for Redis.
    """
    for cached in CACHES:
        cached.cache_clear()
    try:
        with TestClient(create_app()) as instance:
            yield instance
    finally:
        for cached in CACHES:
            cached.cache_clear()


def updating(**values: Any) -> Callable[[uuid.UUID], None]:
    """Force `analyses` columns through Core, so JSONB binds as a document."""

    def apply(analysis_id: uuid.UUID) -> None:
        async def write() -> None:
            engine = create_async_engine(DATABASE_URL or "")
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        sa.update(analyses).where(analyses.c.id == analysis_id).values(**values)
                    )
            finally:
                await engine.dispose()

        run(write)

    return apply


def completed(client: TestClient, **overrides: Any) -> str:
    """An analysis in the one state a return code may be minted from.

    Forced with an UPDATE rather than driven through the pipeline: reaching
    `completed` honestly needs two uploads, a worker, a card to confirm and a
    configuration, none of which this endpoint depends on.
    `test_economic_configuration_endpoint.py` sets the precedent.
    """
    analysis_id = client.post("/analyses").json()["id"]
    updating(
        status="completed",
        card_id=CARD_ID,
        grade_predictions=PREDICTIONS,
        model_bundle_version="condition-compose-v0.1.0+grading-heuristic-v0.1.0",
        grading_rules_version="psa-rules-2026-08-24",
        **overrides,
    )(uuid.UUID(analysis_id))
    return str(analysis_id)


# ---------------------------------------------------------------------------
# The acceptance criterion
# ---------------------------------------------------------------------------
def test_a_completed_analysis_becomes_a_code_that_accepts_one_grade(
    client: TestClient,
) -> None:
    """#270's acceptance criterion, end to end and in one test.

    A user finishes an analysis, asks to be able to report the grade later, and
    weeks afterwards — with no session left — uses the code to say what the slab
    said.
    """
    analysis_id = completed(client)

    minted = client.post(f"/analyses/{analysis_id}/feedback")
    assert minted.status_code == 201
    code = minted.json()["return_code"]

    # Weeks later, in a browser that has never seen this service.
    with another_visitor() as later:
        snapshot = later.get(f"/feedback/{code}")
        assert snapshot.status_code == 200
        assert snapshot.json()["predictions"] == PREDICTIONS
        assert snapshot.json()["card_id"] == str(CARD_ID)

        reported = later.post(f"/feedback/{code}", json=ANSWER)
        assert reported.status_code == 200
        assert reported.json()["grade"] == "9"
        assert reported.json()["status"] == "submitted"

    stored = querying(
        "SELECT grade FROM grade_feedback WHERE card_id = :id",
        id=CARD_ID,
    )
    assert stored == "9"


def test_the_row_holds_no_session_no_image_and_no_address(client: TestClient) -> None:
    """The other half of the acceptance criterion, asserted against the row."""
    analysis_id = completed(client)
    client.post(f"/analyses/{analysis_id}/feedback")

    columns = querying(
        "SELECT string_agg(column_name, ',' ORDER BY column_name) "
        "FROM information_schema.columns WHERE table_name = 'grade_feedback'"
    )

    for forbidden in ("session", "analysis", "image", "address"):
        assert forbidden not in columns


# ---------------------------------------------------------------------------
# Minting
# ---------------------------------------------------------------------------
def test_a_code_is_minted_once(client: TestClient) -> None:
    """It was shown once and cannot be reproduced, so a second ask is a 409."""
    analysis_id = completed(client)

    assert client.post(f"/analyses/{analysis_id}/feedback").status_code == 201
    second = client.post(f"/analyses/{analysis_id}/feedback")

    assert second.status_code == 409
    assert "already been minted" in second.json()["detail"]


@pytest.mark.parametrize("state", ["uploaded", "analyzing", "awaiting_confirmation", "failed"])
def test_only_a_completed_analysis_can_be_minted_over(client: TestClient, state: str) -> None:
    """`completed` is the first state at which a recommendation exists (#244)."""
    analysis_id = completed(client)
    failure = (
        {"failure_code": "analysis_failed", "failure_reason": "job_dead_lettered"}
        if state == "failed"
        else {}
    )
    updating(status=state, **failure)(uuid.UUID(analysis_id))

    response = client.post(f"/analyses/{analysis_id}/feedback")

    assert response.status_code == 409
    assert state in response.json()["detail"]


def test_another_sessions_analysis_is_the_bare_404(client: TestClient) -> None:
    """The same 404 every analysis route answers: no identifier is discoverable."""
    analysis_id = completed(client)

    with another_visitor() as stranger:
        stranger.post("/analyses")
        response = stranger.post(f"/analyses/{analysis_id}/feedback")

    assert response.status_code == 404
    assert response.json() == {"detail": "No analysis is recorded under that identifier."}


def test_an_analysis_with_no_stored_prediction_is_refused(client: TestClient) -> None:
    """A label with nothing to compare against would be useless to spec §67."""
    analysis_id = completed(client)
    updating(grade_predictions=None)(uuid.UUID(analysis_id))

    response = client.post(f"/analyses/{analysis_id}/feedback")

    assert response.status_code == 409
    assert "nothing for a later grade" in response.json()["detail"]


def test_the_code_is_never_returned_again(client: TestClient) -> None:
    """Only the digest is stored, so nothing can produce the code twice."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    body = client.get(f"/feedback/{code}").json()

    assert "return_code" not in body
    stored = querying("SELECT return_code_hash FROM grade_feedback WHERE card_id = :id", id=CARD_ID)
    assert code not in stored
    assert len(stored) == 64


def test_the_snapshot_carries_the_versions_that_dated_it(client: TestClient) -> None:
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    body = client.get(f"/feedback/{code}").json()

    assert body["model_bundle_version"] == "condition-compose-v0.1.0+grading-heuristic-v0.1.0"
    assert body["grading_rules_version"] == "psa-rules-2026-08-24"


def test_the_snapshot_is_not_cached(client: TestClient) -> None:
    """It is served on a bearer capability carried in the path."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    assert client.get(f"/feedback/{code}").headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# Every way of missing, and that they look the same
# ---------------------------------------------------------------------------
def _missing() -> str:
    return "ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ"


def test_an_unknown_code_is_the_bare_404(client: TestClient) -> None:
    response = client.get(f"/feedback/{_missing()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "No feedback is recorded under that code."}


def test_a_malformed_code_answers_exactly_as_an_unknown_one(client: TestClient) -> None:
    """Otherwise the refusal itself tells a guesser their guess was well-formed."""
    malformed = client.get("/feedback/not-a-code")
    unknown = client.get(f"/feedback/{_missing()}")

    assert malformed.status_code == unknown.status_code == 404
    assert malformed.json() == unknown.json()


def test_an_expired_code_answers_the_same(client: TestClient) -> None:
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]
    executing(
        # Both columns: `expires_after_it_was_created` refuses an expiry moved
        # below the creation it belongs to, so an expired row is an *old* row.
        "UPDATE grade_feedback SET created_at = now() - interval '200 days', "
        "expires_at = now() - interval '1 day' WHERE card_id = :id",
        id=CARD_ID,
    )

    response = client.get(f"/feedback/{code}")

    assert response.status_code == 404
    assert response.json() == {"detail": "No feedback is recorded under that code."}


def test_a_code_is_spent_by_the_answer(client: TestClient) -> None:
    """There is no edit path: a wrong grade is a new code from a new analysis."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]
    assert client.post(f"/feedback/{code}", json=ANSWER).status_code == 200

    second = client.post(f"/feedback/{code}", json=ANSWER)
    read_back = client.get(f"/feedback/{code}")

    assert second.status_code == 404
    assert read_back.status_code == 404


def test_a_code_written_down_by_hand_still_works(client: TestClient) -> None:
    """The whole reason the alphabet excludes `I`, `L` and `O`."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    response = client.get(f"/feedback/{code.lower().replace('-', ' ')}")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------
def test_a_grade_that_company_does_not_issue_is_refused(client: TestClient) -> None:
    """PSA issues no 9.5 — the scale is Python's, not a CHECK (#165)."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    response = client.post(f"/feedback/{code}", json={"grading_company": "psa", "grade": "9.5"})

    assert response.status_code == 422


def test_bgs_does_issue_a_half_grade(client: TestClient) -> None:
    """The same value, the other company. This is why the CHECK is the grammar."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    response = client.post(f"/feedback/{code}", json={"grading_company": "bgs", "grade": "9.5"})

    assert response.status_code == 200


def test_a_bad_grade_is_refused_before_the_code_is_looked_up(client: TestClient) -> None:
    """Otherwise a 422 rather than a 404 would confirm the code exists."""
    known = client.post(f"/feedback/{_missing()}", json={"grading_company": "psa", "grade": "9.5"})

    assert known.status_code == 422


def test_a_designation_alone_is_a_whole_answer(client: TestClient) -> None:
    """PSA issues `authentic` *in place of* a grade (#165)."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    response = client.post(
        f"/feedback/{code}", json={"grading_company": "psa", "designation": "authentic"}
    )

    assert response.status_code == 200
    assert response.json()["grade"] is None
    assert response.json()["designation"] == "authentic"


def test_an_answer_naming_nothing_is_refused(client: TestClient) -> None:
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    response = client.post(f"/feedback/{code}", json={"grading_company": "psa"})

    assert response.status_code == 422


def test_a_certification_number_is_optional(client: TestClient) -> None:
    """A person answering weeks later may not have the slab to hand."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    response = client.post(f"/feedback/{code}", json={"grading_company": "psa", "grade": "9"})

    assert response.status_code == 200
    assert response.json()["certification_number"] is None


def test_answering_needs_no_session(client: TestClient) -> None:
    """Weeks later there is none, and spec §53 forbids the account that would
    otherwise carry the identity."""
    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    with another_visitor() as stranger:
        response = stranger.post(f"/feedback/{code}", json=ANSWER)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------
def test_the_sweep_deletes_an_expired_row_and_its_code_then_misses(
    client: TestClient,
) -> None:
    """The exemption's other half: 180 days is a horizon, not an exemption from one."""
    from tcg_api.database import create_session_factory
    from tcg_api.feedback.store import sweep_expired

    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]
    executing(
        # Both columns: `expires_after_it_was_created` refuses an expiry moved
        # below the creation it belongs to, so an expired row is an *old* row.
        "UPDATE grade_feedback SET created_at = now() - interval '200 days', "
        "expires_at = now() - interval '1 day' WHERE card_id = :id",
        id=CARD_ID,
    )

    async def sweep() -> int:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with create_session_factory(engine)() as db:
                return await sweep_expired(db, limit=200)
        finally:
            await engine.dispose()

    assert run(sweep) == 1
    assert client.get(f"/feedback/{code}").status_code == 404
    assert querying("SELECT count(*) FROM grade_feedback WHERE card_id = :id", id=CARD_ID) == 0


def test_the_sweep_leaves_a_live_row_alone(client: TestClient) -> None:
    from tcg_api.database import create_session_factory
    from tcg_api.feedback.store import sweep_expired

    analysis_id = completed(client)
    code = client.post(f"/analyses/{analysis_id}/feedback").json()["return_code"]

    async def sweep() -> int:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with create_session_factory(engine)() as db:
                return await sweep_expired(db, limit=200)
        finally:
            await engine.dispose()

    assert run(sweep) == 0
    assert client.get(f"/feedback/{code}").status_code == 200


def test_the_session_sweep_leaves_the_feedback_row_standing(client: TestClient) -> None:
    """The exemption, asserted rather than described.

    Everything the session held goes at seven days; the prediction snapshot does
    not, because it holds nothing the session held.
    """
    analysis_id = completed(client)
    client.post(f"/analyses/{analysis_id}/feedback")
    executing(
        # Both columns again: `analysis_sessions` carries the same rule.
        "UPDATE analysis_sessions SET created_at = now() - interval '8 days', "
        "expires_at = now() - interval '1 day' "
        "WHERE id = (SELECT session_id FROM analyses WHERE id = :id)",
        id=uuid.UUID(analysis_id),
    )

    executing("DELETE FROM analysis_sessions WHERE expires_at < now()")

    assert querying("SELECT count(*) FROM analyses WHERE id = :id", id=uuid.UUID(analysis_id)) == 0
    assert querying("SELECT count(*) FROM grade_feedback WHERE card_id = :id", id=CARD_ID) == 1


def test_the_expiry_is_the_configured_period(client: TestClient) -> None:
    analysis_id = completed(client)

    minted = client.post(f"/analyses/{analysis_id}/feedback").json()

    expires = datetime.fromisoformat(minted["expires_at"])
    expected = datetime.now(UTC) + timedelta(seconds=get_settings().feedback_ttl_seconds)
    assert abs((expires - expected).total_seconds()) < 60
