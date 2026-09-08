"""Reading and writing spec §68's feedback rows — issue #270.

`tables.py` says what a row *is*; this module is the only place one is written
or read. The split is `analysis/sessions.py`'s: DDL there, statements here.

**Nothing here commits except the sweep**, which has no router above it. The
router owns the transaction, so a mint and the `analyses.feedback_minted_at`
that makes it unrepeatable land together or not at all.

**One lookup rule serves all three routes**, and that is what makes a code's
misses indistinguishable: :func:`read_awaiting` matches on the digest, on the
row not having expired, and on nobody having answered yet. Unknown, expired and
already-answered all come back as `None`, and the caller answers one bare 404 —
`resolve_session`'s rule, for its reason. A code is spent by the answer.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.errors import InvalidGrade
from tcg_domain.grade import Grade
from tcg_grading_companies import DESIGNATIONS, Designation
from tcg_grading_companies.companies import ADAPTERS

from tcg_api.database import execute as _execute
from tcg_api.feedback.tables import FeedbackStatus, grade_feedback

__all__ = [
    "SWEEP_LIMIT",
    "FeedbackRecord",
    "FeedbackStatus",
    "FeedbackStoreUnavailable",
    "GradeFeedbackRefused",
    "execute",
    "mint_feedback",
    "pending",
    "read_awaiting",
    "record_answer",
    "review",
    "sweep_expired",
    "verify_answer",
]

logger = structlog.get_logger(__name__)

#: How many expired rows one sweep will take. A constant rather than a setting,
#: `retention.SWEEP_LIMIT`'s argument: the period is the policy and already has
#: an environment variable; this is a batch size nobody reviews.
SWEEP_LIMIT: int = 200


class FeedbackStoreUnavailable(ConnectionError):
    """The feedback store could not be reached.

    An ordinary `ConnectionError`, `AnalysisStoreUnavailable`'s sibling and for
    its reason: the HTTP layer answers 503 without having to know that the store
    happens to be PostgreSQL.
    """


class GradeFeedbackRefused(ValueError):
    """The reported grade is not one that company issues.

    A `ValueError` so that a pydantic validator turns it into FastAPI's own 422
    — request validation stays outside spec §66's taxonomy (`errors.py`).
    """


async def execute(db: AsyncSession, statement: Any) -> sa.Result[Any]:
    """Run `statement`, turning a driver failure into this domain's exception.

    One place, so that every route in this domain raises the same 503 reason.
    """
    return await _execute(
        db,
        statement,
        unavailable=FeedbackStoreUnavailable,
        message="The feedback store could not be reached.",
    )


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    """One `grade_feedback` row, as far as the HTTP surface cares.

    **Carries no code and no digest.** The digest is the lookup key, so a record
    holding one would put it a `repr()` away from a log line, and the code
    itself exists only in the response that minted it.
    """

    id: uuid.UUID
    card_id: uuid.UUID
    predictions: Mapping[str, Any]
    model_bundle_version: str | None
    grading_rules_version: str | None
    recommended_action: str | None
    status: str
    created_at: datetime
    expires_at: datetime
    grading_company: str | None
    grade: str | None
    designation: str | None
    certification_number: str | None
    submitted_at: datetime | None


_COLUMNS = (
    grade_feedback.c.id,
    grade_feedback.c.card_id,
    grade_feedback.c.predictions,
    grade_feedback.c.model_bundle_version,
    grade_feedback.c.grading_rules_version,
    grade_feedback.c.recommended_action,
    grade_feedback.c.status,
    grade_feedback.c.created_at,
    grade_feedback.c.expires_at,
    grade_feedback.c.grading_company,
    grade_feedback.c.grade,
    grade_feedback.c.designation,
    grade_feedback.c.certification_number,
    grade_feedback.c.submitted_at,
)


def _record(row: Any) -> FeedbackRecord:
    return FeedbackRecord(
        id=row.id,
        card_id=row.card_id,
        predictions=dict(row.predictions),
        model_bundle_version=row.model_bundle_version,
        grading_rules_version=row.grading_rules_version,
        recommended_action=row.recommended_action,
        status=row.status,
        created_at=row.created_at,
        expires_at=row.expires_at,
        grading_company=row.grading_company,
        grade=row.grade,
        designation=row.designation,
        certification_number=row.certification_number,
        submitted_at=row.submitted_at,
    )


def verify_answer(*, grading_company: str, grade: str | None, designation: str | None) -> None:
    """Refuse a report no slab could carry, before any row is touched.

    The per-company scale is checked **here rather than in a CHECK** — #165's
    rule and for its reason: PSA and TAG issue no 9.5 and BGS does, and saying
    so in the schema would make a fourth company cost a migration. The column's
    CHECK is the grammar; this is the scale.

    **A company with no adapter is accepted**, `outcomes.py`'s rule: refusing
    would quietly make `ADAPTERS` the closed set of companies this product
    knows, and the table's own membership CHECK is what holds V1 to three.

    This duplicates `datasets/outcomes.py`'s two private helpers rather than
    importing them, on purpose. `verify_outcome` requires a certification
    number and takes four subgrades, neither of which is this rule — and
    importing `tcg_api.datasets` from the request path is the direction the
    §68 purity test exists to refuse.
    `ponytail: two readings of one rule. Hoist both into
    packages/grading-companies when a third caller appears.`

    Raises:
        GradeFeedbackRefused: Named in a sentence an operator or a user can act on.
    """
    if grade is None and designation is None:
        raise GradeFeedbackRefused("a report names a grade, a designation, or both")

    adapter = ADAPTERS.get(grading_company)

    if grade is not None:
        try:
            parsed = Grade.parse(grade)
        except InvalidGrade as error:
            raise GradeFeedbackRefused(f"{grade} is not a grade") from error
        if parsed.is_bucket:
            raise GradeFeedbackRefused(
                f"{grade} is a range rather than a grade; a slab prints one point"
            )
        if adapter is not None and not adapter.get_grade_scale().supports(parsed):
            issued = ", ".join(str(point) for point in adapter.get_grade_scale().ordered)
            raise GradeFeedbackRefused(
                f"{grading_company} does not issue grade {grade}; its scale is {issued}"
            )

    if designation is not None:
        try:
            named = Designation(designation)
        except ValueError as error:
            raise GradeFeedbackRefused(f"{designation} is not a designation") from error
        issuable = DESIGNATIONS.get(grading_company)
        if issuable is not None and named not in issuable:
            raise GradeFeedbackRefused(f"{grading_company} does not issue {designation}")


async def mint_feedback(
    db: AsyncSession,
    *,
    return_code_hash: str,
    card_id: uuid.UUID,
    predictions: Mapping[str, Any],
    model_bundle_version: str | None,
    grading_rules_version: str | None,
    recommended_action: str | None,
    ttl_seconds: int,
) -> FeedbackRecord:
    """Store one prediction snapshot under `return_code_hash`.

    `expires_at` is computed here rather than defaulted in the schema, so the
    period lives where a reviewer reads it — `create_session`'s rule.

    Does not commit. The caller owns the transaction, so the row and the
    `analyses.feedback_minted_at` that makes it unrepeatable land together.
    """
    statement = (
        sa.insert(grade_feedback)
        .values(
            id=uuid.uuid4(),
            return_code_hash=return_code_hash,
            card_id=card_id,
            predictions=dict(predictions),
            model_bundle_version=model_bundle_version,
            grading_rules_version=grading_rules_version,
            recommended_action=recommended_action,
            status=FeedbackStatus.AWAITING.value,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        .returning(*_COLUMNS)
    )
    result = await execute(db, statement)
    return _record(result.one())


#: A code is live when it names a row, that row has not expired, and nobody has
#: answered yet. Compared against the database's clock, never this process's —
#: `retention.py`'s rule, so a skewed application host cannot extend or shorten
#: anybody's window.
def _live(return_code_hash: str) -> sa.ColumnElement[bool]:
    return sa.and_(
        grade_feedback.c.return_code_hash == return_code_hash,
        grade_feedback.c.expires_at > sa.func.now(),
        grade_feedback.c.status == FeedbackStatus.AWAITING.value,
    )


async def read_awaiting(db: AsyncSession, return_code_hash: str) -> FeedbackRecord | None:
    """The snapshot this code addresses, or `None` if it addresses none now.

    `None` covers every way a code can miss — it names no row, the row expired,
    or the code has already been spent on an answer — and the caller cannot tell
    those apart from here. That is deliberate: all three answer one bare 404, so
    a well-formed guess learns nothing a malformed one would not.
    """
    result = await execute(db, sa.select(*_COLUMNS).where(_live(return_code_hash)))
    row = result.one_or_none()
    return None if row is None else _record(row)


async def record_answer(
    db: AsyncSession,
    *,
    return_code_hash: str,
    grading_company: str,
    grade: str | None,
    designation: str | None,
    certification_number: str | None,
) -> FeedbackRecord | None:
    """Record the grade this card actually received. Returns `None` if it could not.

    One conditional `UPDATE` against the same `_live` predicate, which is the
    race guard as well as the rule: a second answer matches no row, returns
    `None` and is the same 404 as an unknown code. There is no edit path and
    there will not be one — a wrong grade is a new code from a new analysis.

    Does not commit.
    """
    statement = (
        sa.update(grade_feedback)
        .where(_live(return_code_hash))
        .values(
            status=FeedbackStatus.SUBMITTED.value,
            grading_company=grading_company,
            grade=grade,
            designation=designation,
            certification_number=certification_number,
            submitted_at=datetime.now(UTC),
        )
        .returning(*_COLUMNS)
    )
    result = await execute(db, statement)
    row = result.one_or_none()
    return None if row is None else _record(row)


async def pending(db: AsyncSession, *, limit: int) -> tuple[FeedbackRecord, ...]:
    """Every report awaiting spec §68's validation, oldest first.

    The review command's only way in. A return code is the user's and is stored
    only as a digest, so an operator finds a row by its identifier or not at all.
    """
    statement = (
        sa.select(*_COLUMNS)
        .where(grade_feedback.c.status == FeedbackStatus.SUBMITTED.value)
        .order_by(grade_feedback.c.submitted_at)
        .limit(limit)
    )
    result = await execute(db, statement)
    return tuple(_record(row) for row in result)


async def review(db: AsyncSession, feedback_id: uuid.UUID, *, decision: FeedbackStatus) -> bool:
    """Record §68's validation step. Returns whether this call is what moved it.

    `False` covers every way it might not: no row under that identifier, or a
    row that is not `submitted` — never answered, or already reviewed. The
    caller cannot tell those apart and does not need to, because the answer to
    all of them is the same: list what is pending.

    Does not commit.
    """
    if decision not in {FeedbackStatus.VALIDATED, FeedbackStatus.REJECTED}:
        raise ValueError(f"{decision} is not a review decision")

    statement = sa.update(grade_feedback).where(
        grade_feedback.c.id == feedback_id,
        grade_feedback.c.status == FeedbackStatus.SUBMITTED.value,
    )
    # `execute` is typed for the reads it was written for; an UPDATE always
    # produces a `CursorResult`, which is the only kind that counts rows.
    # `state.transition`'s cast, and unquoted for its reason.
    result = cast(sa.CursorResult[Any], await execute(db, statement.values(status=decision.value)))
    return result.rowcount == 1


#: The rows now past their expiry, oldest first — the query
#: `ix_grade_feedback_expires_at` was declared for. The database's clock again.
_DUE = (
    sa.select(grade_feedback.c.id)
    .where(grade_feedback.c.expires_at < sa.func.now())
    .order_by(grade_feedback.c.expires_at)
)


async def sweep_expired(db: AsyncSession, *, limit: int) -> int:
    """Delete every expired return code. Returns how many went.

    One statement, where the session sweep needs a transaction per session.
    That difference is `docs/retention.md`'s exemption restated as code: this
    row names no object, so there is nothing to delete before it and "objects
    before rows" has nothing to order. Deleting one loses a user's answer along
    with the question, which is the intended trade — a label kept past its
    stated horizon is a label kept for a purpose nobody wrote down.
    """
    statement = sa.delete(grade_feedback).where(
        grade_feedback.c.id.in_(_DUE.limit(limit).scalar_subquery())
    )
    result = cast(sa.CursorResult[Any], await execute(db, statement))
    swept = result.rowcount
    await db.commit()

    # A count, and nothing else. `retention.swept`'s rule: a return code on a
    # log line is a line that can answer somebody else's question.
    logger.info("retention.feedback_swept", count=swept)
    return int(swept)
