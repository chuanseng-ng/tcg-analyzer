"""Record what one grading company issued for one physical card.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    uv run tcg-record-grading-outcome \\
        --physical-copy 6f0b1e3c-... \\
        --company psa \\
        --certification-number 12345678 \\
        --grade 9 \\
        --returned-at 2026-09-30

One invocation records one submission's outcome, against the copy identifier
`tcg-ingest-training-images` printed. There is deliberately no HTTP route:
nothing in the dataset domain is a consumer surface, and the operator has a slab
in their hand.

Two things here are not obvious from the table.

**The per-company scale is enforced here rather than in a CHECK.** PSA and TAG
issue eighteen grades and no 9.5; BGS issues nineteen and has one. A CHECK that
knew that would make a fourth company, or a scale revision, cost a migration of
`grading_outcomes` — `market_observations`' split, and the reason the constraint
there is the grade *grammar*. The guard is :func:`verify_outcome`, which reads
`GradeScale.supports` off the company's own adapter. `validated_grade_key` in
`packages/market-data` calls the same method and is deliberately not reused: it
raises `InvalidMarketObservation`, which is the wrong sentence on this path.

**A certification is written back onto the copy only where the copy carries
none.** #153 left `physical_copies` mutable precisely for this write, and
`uq_physical_copies_certification` is what then catches one slab entered as two
copies. Where the copy already names a *different* certification — a card
cross-graded by a second company, which spec's V1 boundary excludes as a feature
but cannot stop existing as data — the copy keeps what it has and this says so.
Overwriting would silently move spec §32's grouping key; refusing would make the
second submission unrecordable.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from tcg_domain.errors import InvalidGrade
from tcg_domain.grade import Grade
from tcg_grading_companies import ADAPTERS, DESIGNATIONS, Designation

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.tables import SUBGRADE_COLUMNS, grading_outcomes, physical_copies
from tcg_api.logging import configure_logging

__all__ = [
    "GradingOutcomeRefused",
    "RecordedOutcome",
    "main",
    "record_outcome",
    "verify_outcome",
]

logger = logging.getLogger(__name__)

_OUTCOME_UNIQUE: Final = "uq_grading_outcomes_certification"
_COPY_UNIQUE: Final = "uq_physical_copies_certification"


class GradingOutcomeRefused(ValueError):
    """An outcome this domain's own rules refuse, named in a sentence."""


@dataclass(frozen=True, slots=True)
class RecordedOutcome:
    """What one invocation did, so the caller can say it rather than guess it."""

    id: uuid.UUID
    #: True when the certification was written onto the `physical_copies` row.
    certified_the_copy: bool
    #: The certification the copy already carried and kept, where it differed.
    copy_keeps: tuple[str, str] | None = None


def verify_outcome(
    *,
    company: str,
    certification_number: str,
    grade: str | None,
    designation: str | None,
    subgrades: tuple[str, ...] = (),
) -> None:
    """Say in words what the constraints and the scales would refuse in names.

    The rules are the table's and the company's, restated rather than replaced:
    an outcome that slipped past this still meets the CHECK constraints, which
    is the guarantee.

    A company with **no adapter** is accepted, deliberately. `GradingCompany` is
    a vocabulary rather than a closed set so that spec §22's "a fourth company
    costs one new adapter and no caller change" stays true, and refusing here
    would quietly make `ADAPTERS` the closed set of valid companies —
    `validated_grade_key`'s own reasoning. The table's CHECK still holds the
    three V1 ships to their slugs.

    Raises:
        GradingOutcomeRefused: Naming the first rule the outcome breaks.
    """
    if not certification_number.strip():
        raise GradingOutcomeRefused(
            "certification_number is blank; a slab prints one and it is what "
            "uq_physical_copies_certification looks a copy up by"
        )
    if grade is None and designation is None:
        raise GradingOutcomeRefused(
            "a submission carrying neither a grade nor a designation is not a submission. "
            "PSA issues 'authentic' in place of a grade; pass --grade or --designation"
        )
    if len(subgrades) not in (0, 4):
        raise GradingOutcomeRefused(
            f"subgrades are all four or none, got {len(subgrades)}: a partial set says "
            "nothing about the three it omits"
        )
    if designation is not None:
        _verify_designation(company, designation)
    for value in (grade, *subgrades):
        if value is not None:
            _verify_grade(company, value)


def _verify_grade(company: str, value: str) -> None:
    try:
        grade = Grade.parse(value)
    except InvalidGrade as error:
        raise GradingOutcomeRefused(f"{value!r} is not a grade: {error}") from error
    if grade.is_bucket:
        raise GradingOutcomeRefused(
            f"{value!r} collapses a tail, which is what a model emits when it will not "
            "commit to one point. A slab prints one point"
        )
    adapter = ADAPTERS.get(company)
    if adapter is not None and not adapter.get_grade_scale().supports(grade):
        scale = adapter.get_grade_scale()
        raise GradingOutcomeRefused(
            f"{company} does not issue grade {grade}; its scale is "
            f"{', '.join(str(item) for item in scale.ordered)}"
        )


def _verify_designation(company: str, value: str) -> None:
    try:
        designation = Designation(value)
    except ValueError as error:
        raise GradingOutcomeRefused(
            f"{value!r} is not a designation; the five are "
            f"{', '.join(str(member) for member in Designation)}"
        ) from error
    issued = DESIGNATIONS.get(company)
    if issued is not None and designation not in issued:
        raise GradingOutcomeRefused(
            f"{company} does not issue {designation}; it issues "
            f"{', '.join(sorted(str(member) for member in issued))}"
        )


async def record_outcome(
    connection: AsyncConnection,
    *,
    physical_copy_id: uuid.UUID,
    company: str,
    certification_number: str,
    grade: str | None = None,
    designation: str | None = None,
    subgrades: tuple[str, ...] = (),
    returned_at: date | None = None,
) -> RecordedOutcome:
    """Certify the copy and write one outcome, in the caller's transaction.

    **The copy is written first, and the order is what makes the two refusals
    distinguishable.** One slab claimed by a *second* physical card trips
    `uq_physical_copies_certification` on the write-back; the *same* slab
    recorded twice against one card gets past that — the copy already carries
    exactly this certification — and trips
    `uq_grading_outcomes_certification` on the insert. Inserting first would
    collapse both onto the second constraint and leave the first unreachable,
    which is the one #153 declared for this and nothing had yet written to.

    Raises:
        GradingOutcomeRefused: If `verify_outcome` refuses it, or the copy does
            not exist.
        IntegrityError: If its certification is already on a different copy
            (`uq_physical_copies_certification`) or this slab is already
            recorded (`uq_grading_outcomes_certification`).
    """
    verify_outcome(
        company=company,
        certification_number=certification_number,
        grade=grade,
        designation=designation,
        subgrades=subgrades,
    )
    certified, keeps = await _certify(
        connection,
        physical_copy_id=physical_copy_id,
        company=company,
        certification_number=certification_number,
    )

    outcome_id = uuid.uuid4()
    # Nothing when none were issued: the four columns default to NULL, and
    # `subgrades_are_four_or_none` is what keeps a half-set out either way.
    issued = dict(zip(SUBGRADE_COLUMNS, subgrades, strict=True)) if subgrades else {}
    await connection.execute(
        sa.insert(grading_outcomes).values(
            id=outcome_id,
            physical_copy_id=physical_copy_id,
            grading_company=company,
            certification_number=certification_number,
            grade=grade,
            designation=designation,
            returned_at=returned_at,
            **issued,
        )
    )
    return RecordedOutcome(id=outcome_id, certified_the_copy=certified, copy_keeps=keeps)


async def _certify(
    connection: AsyncConnection,
    *,
    physical_copy_id: uuid.UUID,
    company: str,
    certification_number: str,
) -> tuple[bool, tuple[str, str] | None]:
    """Write the certification onto the copy, where the copy carries none.

    The copy is read before anything is written, so a copy identifier that names
    nothing is a sentence rather than a foreign-key violation. The key is still
    the guarantee.
    """
    existing = (
        await connection.execute(
            sa.select(
                physical_copies.c.certification_company, physical_copies.c.certification_number
            ).where(physical_copies.c.id == physical_copy_id)
        )
    ).one_or_none()
    if existing is None:
        raise GradingOutcomeRefused(
            f"{physical_copy_id} names no physical copy; the identifier is the one "
            "tcg-ingest-training-images printed"
        )
    if existing.certification_company is None:
        await connection.execute(
            sa.update(physical_copies)
            .where(physical_copies.c.id == physical_copy_id)
            .values(certification_company=company, certification_number=certification_number)
        )
        return True, None
    carried = (existing.certification_company, existing.certification_number)
    if carried == (company, certification_number):
        return False, None
    return False, carried


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--physical-copy",
        required=True,
        type=uuid.UUID,
        help="the copy identifier tcg-ingest-training-images printed",
    )
    parser.add_argument("--company", required=True, help="the company that issued it — 'psa'")
    parser.add_argument(
        "--certification-number", required=True, help="the number printed on the slab"
    )
    parser.add_argument("--grade", help="what was issued — '9', '9.5'. Omit for a designation")
    parser.add_argument(
        "--designation",
        help="a label that is not a point on the scale — 'authentic', 'black_label'",
    )
    for column in SUBGRADE_COLUMNS:
        axis = column.removeprefix("subgrade_")
        parser.add_argument(f"--subgrade-{axis}", help=f"BGS's {axis} subgrade. All four or none")
    parser.add_argument(
        "--returned-at",
        type=date.fromisoformat,
        help="when the slab came back. Omit for a slab this project did not submit",
    )
    return parser


def _validated(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    try:
        verify_outcome(
            company=arguments.company,
            certification_number=arguments.certification_number,
            grade=arguments.grade,
            designation=arguments.designation,
            subgrades=_subgrades(arguments),
        )
    except GradingOutcomeRefused as refusal:
        parser.error(str(refusal))


def _subgrades(arguments: argparse.Namespace) -> tuple[str, ...]:
    """The subgrades that were passed, in `SUBGRADE_COLUMNS` order.

    A partial set comes back short rather than padded, which is what lets
    `verify_outcome` say "all four or none" before the CHECK has to.
    """
    values = tuple(getattr(arguments, column) for column in SUBGRADE_COLUMNS)
    return tuple(value for value in values if value is not None)


async def run(arguments: argparse.Namespace) -> RecordedOutcome:
    """Record the outcome the arguments describe, in one transaction."""
    engine = create_engine()
    try:
        async with engine.begin() as connection:
            return await record_outcome(
                connection,
                physical_copy_id=arguments.physical_copy,
                company=arguments.company,
                certification_number=arguments.certification_number,
                grade=arguments.grade,
                designation=arguments.designation,
                subgrades=_subgrades(arguments),
                returned_at=arguments.returned_at,
            )
    finally:
        await engine.dispose()


def main() -> int:
    """Console-script entry point (`uv run tcg-record-grading-outcome`)."""
    parser = _parser()
    arguments = parser.parse_args()
    _validated(parser, arguments)

    configure_logging(get_settings())

    try:
        recorded = asyncio.run(run(arguments))
    except GradingOutcomeRefused as refusal:
        logger.error("grading outcome refused: %s", refusal)
        return 1
    except IntegrityError as conflict:
        if _OUTCOME_UNIQUE in str(conflict.orig):
            logger.error(
                "%s %s is already recorded; one slab is one outcome, so correct the row "
                "rather than recording it twice",
                arguments.company,
                arguments.certification_number,
            )
        elif _COPY_UNIQUE in str(conflict.orig):
            logger.error(
                "%s %s is already on a different physical copy; one slab is one card, and "
                "two copies carrying it is exactly the leakage a train/test split "
                "must not have",
                arguments.company,
                arguments.certification_number,
            )
        else:
            logger.error("grading outcome refused by the database: %s", conflict.orig)
        return 1

    logger.info(
        "recorded %s %s for physical copy %s as outcome %s; %s",
        arguments.company,
        arguments.grade or arguments.designation,
        arguments.physical_copy,
        recorded.id,
        _certification_summary(recorded),
    )
    return 0


def _certification_summary(recorded: RecordedOutcome) -> str:
    if recorded.certified_the_copy:
        return "the certification was written onto the copy"
    if recorded.copy_keeps is None:
        return "the copy already carried this certification"
    company, number = recorded.copy_keeps
    return (
        f"the copy keeps the certification it carried ({company} {number}); "
        "this outcome is recorded against it either way"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
