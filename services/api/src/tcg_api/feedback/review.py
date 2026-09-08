"""Record spec §68's validation step on a grade a user reported — issue #270.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    uv run tcg-review-grade-feedback --pending
    uv run tcg-review-grade-feedback --feedback-id <id> --decision validated

§68's diagram is `user feedback -> validation -> approved dataset -> future
training`, and this command is the second box. It is deliberately a command
rather than a route: the whole point of the arrow is that a person looks at a
report before anything downstream believes it, and a route would be a way for
that to happen automatically.

**It never answers on a user's behalf.** `awaiting -> submitted` is the route's
move alone and there is no flag here that could make it — an operator who could
submit could put words in a user's mouth, and the row would still say
`submitted` as though the user had. What this command may write is
`submitted -> validated` and `submitted -> rejected`, and the database's trigger
holds that even against a hand-run `UPDATE`.

**It never sees a return code.** The code is the user's, stored only as a
digest, and an operator holding one could read and answer somebody's report. So
a row is found by its identifier, and `--pending` is how an identifier is found.

The arrows above are ASCII deliberately: `description=__doc__` reaches
`argparse`, which prints to whatever encoding the operator's console has, and a
Windows console at cp1252 cannot encode a U+2192 arrow.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from sqlalchemy.exc import IntegrityError

from tcg_api.config import get_settings
from tcg_api.database import create_engine, create_session_factory
from tcg_api.feedback.store import FeedbackRecord, FeedbackStatus, pending, review
from tcg_api.logging import configure_logging

__all__ = ["main", "run"]

logger = logging.getLogger("tcg_api.feedback.review")

#: The decisions this command may record. `awaiting` and `submitted` are absent
#: on purpose: the first is where a row is born and the second is the user's.
DECISIONS = (FeedbackStatus.VALIDATED.value, FeedbackStatus.REJECTED.value)

#: How many pending reports one listing shows. A batch size nobody reviews.
LISTING_LIMIT = 50


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--pending",
        action="store_true",
        help="list the reports awaiting review, oldest first",
    )
    parser.add_argument(
        "--feedback-id",
        type=uuid.UUID,
        help="which report to review — an identifier from --pending",
    )
    parser.add_argument(
        "--decision",
        choices=DECISIONS,
        help="whether the report is believed. Both are terminal.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=LISTING_LIMIT,
        help=f"how many pending reports to list (default {LISTING_LIMIT})",
    )
    return parser


def _validated(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    """Refuse before any database work, so a mistake costs a usage message."""
    reviewing = arguments.feedback_id is not None or arguments.decision is not None

    if arguments.pending and reviewing:
        parser.error("--pending lists reports; drop it to review one")
    if not arguments.pending and not reviewing:
        parser.error("nothing to do: pass --pending, or --feedback-id with --decision")
    if reviewing and (arguments.feedback_id is None or arguments.decision is None):
        parser.error("--feedback-id and --decision go together")
    if arguments.limit < 1:
        parser.error("--limit is at least 1")


def describe(record: FeedbackRecord) -> str:
    """One report, on one line. No code and no digest — neither is an operator's."""
    answer = record.grade if record.grade is not None else record.designation
    certification = record.certification_number or "no certification number"
    submitted = "unknown" if record.submitted_at is None else record.submitted_at.isoformat()
    return (
        f"{record.id}  {record.grading_company} {answer}  {certification}  "
        f"reported {submitted}  predicted {record.recommended_action or 'no verdict'}"
    )


async def run(arguments: argparse.Namespace) -> int:
    """List or review, in one transaction. Returns the process's exit code."""
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as db:
            if arguments.pending:
                reports = await pending(db, limit=arguments.limit)
                if not reports:
                    logger.info("no reports are awaiting review")
                    return 0
                for record in reports:
                    logger.info("%s", describe(record))
                return 0

            decision = FeedbackStatus(arguments.decision)
            if not await review(db, arguments.feedback_id, decision=decision):
                logger.error(
                    "no report is awaiting review under %s: it may never have been "
                    "answered, or it may already have been reviewed; run --pending "
                    "to see what is",
                    arguments.feedback_id,
                )
                return 1
            await db.commit()
    finally:
        await engine.dispose()

    logger.info("recorded %s as %s", arguments.feedback_id, arguments.decision)
    return 0


def main() -> int:
    """Console-script entry point (`uv run tcg-review-grade-feedback`)."""
    parser = _parser()
    arguments = parser.parse_args()
    _validated(parser, arguments)

    configure_logging(get_settings())

    try:
        return asyncio.run(run(arguments))
    except IntegrityError as conflict:
        # The status trigger lands here and should never fire: `review`'s own
        # `WHERE status = 'submitted'` has already matched nothing by then.
        logger.error("the review was refused by the database: %s", conflict.orig)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
