"""Spec §68's feedback table against a real PostgreSQL — #270.

`test_feedback_tables.py` asserts what was *declared*; this asserts what the
database actually does after the migration has run. Alembic compares a check's
name but never its text, and no triggers at all — so the refusals here are the
only guard against the declaration and the migration drifting apart.

Most of what is here is the lifecycle, one move per test. That is deliberate
repetition, `test_models_schema.py`'s rule: "the status walks along a branch" is
the rule most likely to be softened later by somebody with a verdict to change,
and a single parametrised "bad transition is refused" would let half the moves
be lost in one edit. The move that matters most is `validated → rejected`,
because that is the one a linear `array_position` ladder would have allowed.

Nothing here truncates. `grade_feedback` names a catalog row, `cards` is shared
with every other suite, and #196's guard exists because a fixture that truncates
is a fixture that can lose a corpus — so this module seeds its own two rows
idempotently and deletes them in foreign-key order.

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
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.catalog.tables import cards, sets
from tcg_api.feedback.tables import grade_feedback

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
    ),
]

SET_ID = uuid.UUID("2f0f1a1e-0000-4000-8000-000000000001")
CARD_ID = uuid.UUID("2f0f1a1e-0000-4000-8000-000000000002")

#: A row every constraint accepts. Each test below spoils exactly one thing, so
#: a failure names the rule that was broken rather than "something was wrong".
LEGAL: dict[str, Any] = {
    "return_code_hash": "a" * 64,
    "card_id": CARD_ID,
    "predictions": {"version": "grading-heuristic-v0.1.0", "predictions": {}},
    "model_bundle_version": "condition-compose-v0.1.0+grading-heuristic-v0.1.0",
    "grading_rules_version": "psa-rules-2026-08-24",
    "recommended_action": "insufficient_information",
    "status": "awaiting",
}

#: The smallest legal answer, for the moves that need one before them.
ANSWER: dict[str, Any] = {
    "status": "submitted",
    "grading_company": "psa",
    "grade": "9",
}


def execute(statement: Any, values: Any = None) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                if values is None:
                    await connection.execute(statement)
                else:
                    await connection.execute(statement, values)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def fetch(statement: Any) -> list[Any]:
    async def scenario() -> list[Any]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return list((await connection.execute(statement)).all())
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` deliberately leaves the database at `base`."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture(autouse=True)
def catalog_row() -> Iterator[None]:
    """The one printed card these rows point at. Deleted in foreign-key order."""
    _clear()
    execute(
        sa.insert(sets),
        {
            "id": SET_ID,
            "game": "pokemon",
            "language": "en",
            "set_code": "feedback-probe",
            "name": "Feedback Probe",
        },
    )
    execute(
        sa.insert(cards),
        {
            "id": CARD_ID,
            "game": "pokemon",
            "language": "en",
            "set_id": SET_ID,
            "card_number": "1/1",
            "name": "Feedback Probe",
        },
    )
    yield
    _clear()


def _clear() -> None:
    execute(sa.delete(grade_feedback).where(grade_feedback.c.card_id == CARD_ID))
    execute(sa.delete(cards).where(cards.c.id == CARD_ID))
    execute(sa.delete(sets).where(sets.c.id == SET_ID))


def insert_feedback(**overrides: Any) -> uuid.UUID:
    identifier = uuid.uuid4()
    execute(
        sa.insert(grade_feedback),
        {
            **LEGAL,
            **overrides,
            "id": identifier,
            "expires_at": datetime.now(UTC) + timedelta(days=180),
        },
    )
    return identifier


def move(feedback_id: uuid.UUID, **values: Any) -> None:
    if values.get("status") == "submitted" and "submitted_at" not in values:
        values["submitted_at"] = datetime.now(UTC)
    execute(sa.update(grade_feedback).where(grade_feedback.c.id == feedback_id).values(**values))


def status_of(feedback_id: uuid.UUID) -> str:
    rows = fetch(sa.select(grade_feedback.c.status).where(grade_feedback.c.id == feedback_id))
    return str(rows[0].status)


# ---------------------------------------------------------------------------
# The row itself
# ---------------------------------------------------------------------------
def test_a_legal_row_is_accepted() -> None:
    feedback_id = insert_feedback()

    rows = fetch(sa.select(grade_feedback).where(grade_feedback.c.id == feedback_id))

    assert rows[0].status == "awaiting"
    assert rows[0].submitted_at is None


def test_a_rendered_code_cannot_be_stored_where_its_digest_belongs() -> None:
    """The property that makes "shown once" true of the database as well."""
    with pytest.raises(IntegrityError, match="return_code_is_stored_as_a_digest"):
        insert_feedback(return_code_hash="A3KDM-9F2QT-BXWR7-N0HJ5")


def test_two_rows_cannot_share_a_code() -> None:
    insert_feedback()

    with pytest.raises(IntegrityError, match="uq_grade_feedback_return_code_hash"):
        insert_feedback()


def test_an_expiry_before_creation_is_refused() -> None:
    with pytest.raises(IntegrityError, match="expires_after_it_was_created"):
        execute(
            sa.insert(grade_feedback),
            {**LEGAL, "id": uuid.uuid4(), "expires_at": datetime.now(UTC) - timedelta(days=1)},
        )


def test_the_card_cannot_be_deleted_while_feedback_names_it() -> None:
    """RESTRICT: nothing deletes from the catalog (#27), and a null card would
    be a prediction about nothing."""
    insert_feedback()

    with pytest.raises(IntegrityError, match="fk_grade_feedback_card_id_cards"):
        execute(sa.delete(cards).where(cards.c.id == CARD_ID))


def test_the_row_can_be_deleted() -> None:
    """The hourly sweep is the whole retention story, so DELETE must work.

    `model_bundles` refuses one; copying that trigger across would make
    `docs/retention.md` a document the schema contradicts.
    """
    feedback_id = insert_feedback()

    execute(sa.delete(grade_feedback).where(grade_feedback.c.id == feedback_id))

    assert fetch(sa.select(grade_feedback.c.id).where(grade_feedback.c.id == feedback_id)) == []


# ---------------------------------------------------------------------------
# The answer — strict both ways
# ---------------------------------------------------------------------------
def test_an_awaiting_row_may_not_carry_an_answer() -> None:
    with pytest.raises(IntegrityError, match="answer_is_recorded_exactly_when_answered"):
        insert_feedback(grading_company="psa", grade="9")


def test_an_answered_row_must_name_a_company() -> None:
    feedback_id = insert_feedback()

    with pytest.raises(IntegrityError, match="answer_is_recorded_exactly_when_answered"):
        move(feedback_id, status="submitted", grade="9")


def test_an_answered_row_needs_a_grade_or_a_designation() -> None:
    feedback_id = insert_feedback()

    with pytest.raises(IntegrityError, match="answer_is_recorded_exactly_when_answered"):
        move(feedback_id, status="submitted", grading_company="psa")


def test_a_designation_alone_is_a_whole_answer() -> None:
    """PSA issues `authentic` *in place of* a grade (#165), so this is honest."""
    feedback_id = insert_feedback()

    move(feedback_id, status="submitted", grading_company="psa", designation="authentic")

    rows = fetch(
        sa.select(grade_feedback.c.grade, grade_feedback.c.designation).where(
            grade_feedback.c.id == feedback_id
        )
    )
    assert rows[0].grade is None
    assert rows[0].designation == "authentic"


def test_a_bucket_is_not_a_grade_a_company_issued() -> None:
    """A slab prints one point; §24's collapsed tails are what a model emits."""
    feedback_id = insert_feedback()

    with pytest.raises(IntegrityError, match="grade_is_an_issued_grade"):
        move(feedback_id, **{**ANSWER, "grade": "9_or_higher"})


def test_a_grade_off_every_scale_is_refused() -> None:
    """The CHECK is the grammar. That `9.5` is PSA-illegal is Python's to say."""
    feedback_id = insert_feedback()

    with pytest.raises(IntegrityError, match="grade_is_an_issued_grade"):
        move(feedback_id, **{**ANSWER, "grade": "10.5"})


def test_a_half_grade_is_grammatical_here() -> None:
    """BGS issues 9.5, so the grammar must take it — a per-company CHECK would
    make a fourth company cost a migration (#165)."""
    feedback_id = insert_feedback()

    move(feedback_id, **{**ANSWER, "grading_company": "bgs", "grade": "9.5"})

    rows = fetch(sa.select(grade_feedback.c.grade).where(grade_feedback.c.id == feedback_id))
    assert rows[0].grade == "9.5"


def test_a_blank_certification_number_is_refused() -> None:
    feedback_id = insert_feedback()

    with pytest.raises(IntegrityError, match="certification_number_is_not_blank"):
        move(feedback_id, **{**ANSWER, "certification_number": "   "})


def test_an_unknown_company_is_refused() -> None:
    feedback_id = insert_feedback()

    with pytest.raises(IntegrityError, match="grading_company_is_supported"):
        move(feedback_id, **{**ANSWER, "grading_company": "cgc"})


def test_an_unknown_recommended_action_is_refused() -> None:
    with pytest.raises(IntegrityError, match="recommended_action_is_a_known_action"):
        insert_feedback(recommended_action="probably")


def test_predictions_must_be_a_document() -> None:
    with pytest.raises(IntegrityError, match="predictions_is_a_document"):
        insert_feedback(predictions=["not", "a", "document"])


# ---------------------------------------------------------------------------
# The lifecycle — one move per test, deliberately
# ---------------------------------------------------------------------------
def test_an_unknown_status_is_refused() -> None:
    """Answered, so that the status CHECK is the only rule left to break.

    A bare `status='pending'` trips the strict-both-ways CHECK first — which is
    correct, and is why the answer is supplied here rather than the membership
    rule being asserted through whichever constraint happens to fire.
    """
    with pytest.raises(IntegrityError, match="status_is_a_known_status"):
        insert_feedback(
            status="pending",
            grading_company="psa",
            grade="9",
            submitted_at=datetime.now(UTC),
        )


def test_awaiting_becomes_submitted() -> None:
    feedback_id = insert_feedback()

    move(feedback_id, **ANSWER)

    assert status_of(feedback_id) == "submitted"


def test_submitted_becomes_validated() -> None:
    feedback_id = insert_feedback()
    move(feedback_id, **ANSWER)

    move(feedback_id, status="validated")

    assert status_of(feedback_id) == "validated"


def test_submitted_becomes_rejected() -> None:
    feedback_id = insert_feedback()
    move(feedback_id, **ANSWER)

    move(feedback_id, status="rejected")

    assert status_of(feedback_id) == "rejected"


def test_awaiting_cannot_skip_to_validated() -> None:
    """Nobody validates a report nobody made."""
    feedback_id = insert_feedback()

    with pytest.raises(IntegrityError, match="cannot move from awaiting to validated"):
        move(feedback_id, status="validated")


def test_awaiting_cannot_skip_to_rejected() -> None:
    feedback_id = insert_feedback()

    with pytest.raises(IntegrityError, match="cannot move from awaiting to rejected"):
        move(feedback_id, status="rejected")


def test_a_validated_verdict_cannot_become_a_rejection() -> None:
    """**The move a linear ladder would have allowed.**

    `array_position` says only "later than", and `validated` and `rejected` sit
    at the same depth — so `model_bundles`' rule would let a reviewer change a
    verdict in place. Spec §68 makes validation a deliberate act, and an
    undoable one is not that.
    """
    feedback_id = insert_feedback()
    move(feedback_id, **ANSWER)
    move(feedback_id, status="validated")

    with pytest.raises(IntegrityError, match="cannot move from validated to rejected"):
        move(feedback_id, status="rejected")


def test_a_rejection_cannot_become_a_validated_verdict() -> None:
    feedback_id = insert_feedback()
    move(feedback_id, **ANSWER)
    move(feedback_id, status="rejected")

    with pytest.raises(IntegrityError, match="cannot move from rejected to validated"):
        move(feedback_id, status="validated")


def test_a_submitted_report_cannot_go_back_to_awaiting() -> None:
    """There is no edit path, and un-answering would be one."""
    feedback_id = insert_feedback()
    move(feedback_id, **ANSWER)

    with pytest.raises(IntegrityError, match="cannot move from submitted to awaiting"):
        move(
            feedback_id,
            status="awaiting",
            grading_company=None,
            grade=None,
            submitted_at=None,
        )
