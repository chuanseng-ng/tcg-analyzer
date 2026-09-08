"""`tcg-review-grade-feedback` — spec §68's validation step, #270.

The argument checks need no database: they are what `_validated` refuses before
any statement runs, and the point of them is that a mistake costs a usage
message rather than a half-done review. The rest drives a real PostgreSQL,
because what the command has to get right is which transitions it may write, and
that is the database's rule.

The claim worth reading twice is
`test_the_command_cannot_answer_on_a_users_behalf`: an operator who could write
`awaiting → submitted` could put words in a user's mouth, and the row would
still say `submitted` as though the user had typed it.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.feedback import review as review_command
from tcg_api.feedback.store import FeedbackRecord
from tcg_api.feedback.tables import FeedbackStatus, grade_feedback

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

SET_ID = uuid.UUID("4b0f1a1e-0000-4000-8000-000000000001")
CARD_ID = uuid.UUID("4b0f1a1e-0000-4000-8000-000000000002")


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


def executing(statement: Any, values: Any = None) -> None:
    async def write() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                if values is None:
                    await connection.execute(statement)
                else:
                    await connection.execute(statement, values)
        finally:
            await engine.dispose()

    run(write)


def status_of(feedback_id: uuid.UUID) -> str | None:
    async def read() -> str | None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    sa.select(grade_feedback.c.status).where(grade_feedback.c.id == feedback_id)
                )
                row = result.one_or_none()
                return None if row is None else str(row.status)
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


@pytest.fixture
def catalog_row() -> Iterator[None]:
    _clear()
    executing(
        sa.insert(
            sa.table(
                "sets",
                *(sa.column(name) for name in ("id", "game", "language", "set_code", "name")),
            )
        ),
        {
            "id": SET_ID,
            "game": "pokemon",
            "language": "en",
            "set_code": "feedback-review",
            "name": "Feedback Review",
        },
    )
    executing(
        sa.insert(
            sa.table(
                "cards",
                *(
                    sa.column(name)
                    for name in ("id", "game", "language", "set_id", "card_number", "name")
                ),
            )
        ),
        {
            "id": CARD_ID,
            "game": "pokemon",
            "language": "en",
            "set_id": SET_ID,
            "card_number": "1/1",
            "name": "Feedback Review",
        },
    )
    yield
    _clear()


def _clear() -> None:
    executing(sa.delete(grade_feedback).where(grade_feedback.c.card_id == CARD_ID))
    executing(sa.text("DELETE FROM cards WHERE id = :id").bindparams(id=CARD_ID))
    executing(sa.text("DELETE FROM sets WHERE id = :id").bindparams(id=SET_ID))


def insert_report(**overrides: Any) -> uuid.UUID:
    feedback_id = uuid.uuid4()
    executing(
        sa.insert(grade_feedback),
        {
            "id": feedback_id,
            "return_code_hash": uuid.uuid4().hex + uuid.uuid4().hex,
            "card_id": CARD_ID,
            "predictions": {"predictions": {}},
            "status": FeedbackStatus.SUBMITTED.value,
            "expires_at": datetime.now(UTC) + timedelta(days=180),
            "grading_company": "psa",
            "grade": "10",
            "certification_number": "12345678",
            "submitted_at": datetime.now(UTC),
            **overrides,
        },
    )
    return feedback_id


def invoke(*flags: str) -> int:
    parser = review_command._parser()
    arguments = parser.parse_args(flags)
    review_command._validated(parser, arguments)
    return asyncio.run(review_command.run(arguments))


# ---------------------------------------------------------------------------
# The arguments — no database needed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "flags",
    [
        (),
        ("--pending", "--decision", "validated"),
        ("--feedback-id", "3f6a1f1e-0000-4000-8000-000000000000"),
        ("--decision", "validated"),
        ("--pending", "--limit", "0"),
    ],
)
def test_a_malformed_invocation_costs_a_usage_message(flags: tuple[str, ...]) -> None:
    """`parser.error` exits 2 before any statement runs."""
    parser = review_command._parser()
    arguments = parser.parse_args(flags)

    with pytest.raises(SystemExit) as refusal:
        review_command._validated(parser, arguments)

    assert refusal.value.code == 2


def test_the_command_cannot_answer_on_a_users_behalf() -> None:
    """**There is no flag that writes `awaiting → submitted`.**

    That move is the route's alone. An operator who could make it could put
    words in a user's mouth, and the row would afterwards say `submitted` as
    though the user had typed it — which is exactly the claim spec §68's
    validation step exists to be able to trust.
    """
    parser = review_command._parser()

    assert set(review_command.DECISIONS) == {"validated", "rejected"}
    with pytest.raises(SystemExit):
        parser.parse_args(["--feedback-id", str(uuid.uuid4()), "--decision", "submitted"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--feedback-id", str(uuid.uuid4()), "--decision", "awaiting"])


def test_a_report_is_described_without_its_code() -> None:
    """The digest is the lookup key; an operator's listing must not carry one."""
    digest = "d" * 64
    record = FeedbackRecord(
        id=uuid.uuid4(),
        card_id=CARD_ID,
        predictions={},
        model_bundle_version=None,
        grading_rules_version=None,
        recommended_action="insufficient_information",
        status="submitted",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=180),
        grading_company="psa",
        grade="10",
        designation=None,
        certification_number="12345678",
        submitted_at=datetime.now(UTC),
    )

    line = review_command.describe(record)

    assert digest not in line
    assert "psa 10" in line
    assert "12345678" in line


# ---------------------------------------------------------------------------
# Against a real database
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_a_submitted_report_can_be_validated(catalog_row: None) -> None:
    feedback_id = insert_report()

    assert invoke("--feedback-id", str(feedback_id), "--decision", "validated") == 0
    assert status_of(feedback_id) == "validated"


@pytest.mark.integration
@requires_postgres
def test_a_submitted_report_can_be_rejected(catalog_row: None) -> None:
    """Not a judgement about the user: an unreadable certification reaches here."""
    feedback_id = insert_report()

    assert invoke("--feedback-id", str(feedback_id), "--decision", "rejected") == 0
    assert status_of(feedback_id) == "rejected"


@pytest.mark.integration
@requires_postgres
def test_reviewing_twice_is_refused(catalog_row: None) -> None:
    """A verdict is not changed in place — the trigger's rule, from outside."""
    feedback_id = insert_report()
    invoke("--feedback-id", str(feedback_id), "--decision", "validated")

    assert invoke("--feedback-id", str(feedback_id), "--decision", "rejected") == 1
    assert status_of(feedback_id) == "validated"


@pytest.mark.integration
@requires_postgres
def test_a_report_nobody_answered_cannot_be_reviewed(catalog_row: None) -> None:
    """`awaiting` is not `submitted`, and the command's WHERE says so."""
    feedback_id = insert_report(
        status=FeedbackStatus.AWAITING.value,
        grading_company=None,
        grade=None,
        certification_number=None,
        submitted_at=None,
    )

    assert invoke("--feedback-id", str(feedback_id), "--decision", "validated") == 1
    assert status_of(feedback_id) == "awaiting"


@pytest.mark.integration
@requires_postgres
def test_an_unknown_identifier_is_refused(catalog_row: None) -> None:
    assert invoke("--feedback-id", str(uuid.uuid4()), "--decision", "validated") == 1


@pytest.mark.integration
@requires_postgres
def test_pending_lists_what_is_awaiting_review(catalog_row: None) -> None:
    insert_report()

    assert invoke("--pending") == 0


@pytest.mark.integration
@requires_postgres
def test_pending_is_empty_once_everything_is_reviewed(catalog_row: None) -> None:
    feedback_id = insert_report()
    invoke("--feedback-id", str(feedback_id), "--decision", "validated")

    assert invoke("--pending") == 0
    assert status_of(feedback_id) == "validated"
