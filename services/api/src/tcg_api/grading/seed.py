"""Write the published PSA/TAG/BGS rules versions into `grading_rules`.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    uv run tcg-seed-grading-rules

**There is no JSON seed file, deliberately.** The catalog seeds are JSON because
a hand-authored card list has no Python home; these records do — they are three
validated `GradingRules` constants in `packages/grading-companies`, each already
carrying its version, its source and the date it was read. A
`database/seeds/grading/rules.json` would be a second source of truth for them,
and a lossy one: it would have to re-encode `effective_from: null` for TAG and
BGS and would need a parser, a schema and an error class whose entire job is to
rebuild objects this process already holds.

The records are read through `ADAPTERS` rather than by naming `PSA_RULES`,
`TAG_RULES` and `BGS_RULES`, so a fourth grading company costs one new adapter
and no edit here — which is the seam spec §22 promises everywhere else.

Writing is `ON CONFLICT DO NOTHING` on the version identifier, matching
`register_version`'s policy and its reasoning: re-running converges, and
rewriting a published version would falsify every analysis that recorded it.
`trg_grading_rules_immutable` refuses a `DO UPDATE` regardless, so the policy is
the database's rather than this module's.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine
from tcg_grading_companies import ADAPTERS, GradingRules

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.grading.tables import grading_rules
from tcg_api.logging import configure_logging

__all__ = [
    "GradingSeedError",
    "apply_grading_rules",
    "load_grading_rules",
    "main",
    "seed",
]

logger = logging.getLogger(__name__)


class GradingSeedError(ValueError):
    """A published record cannot be represented in `grading_rules` as declared."""


def load_grading_rules() -> tuple[GradingRules, ...]:
    """Every adapter's published standard, in company order. Touches no database.

    Raises:
        GradingSeedError: If a record carries an `effective_to`. `grading_rules`
            derives that column from the next version's `effective_from`
            (`tcg_api.grading.tables` gives the argument), so there is nowhere to
            put an explicit one — and silently dropping it would lose the fact
            that a standard was declared to have stopped. Nothing sets one today;
            the point is that the day something does, this fails loudly instead
            of writing a record that means something else.
    """
    records = tuple(
        sorted((adapter.get_rules() for adapter in ADAPTERS.values()), key=lambda r: r.company)
    )
    for record in records:
        if record.effective_to is not None:
            raise GradingSeedError(
                f"{record.version} declares effective_to={record.effective_to}, which "
                "grading_rules derives rather than stores. Publish the successor "
                "version instead, or add the column — see tcg_api.grading.tables."
            )
    return records


async def apply_grading_rules(records: tuple[GradingRules, ...], engine: AsyncEngine) -> None:
    """Write the records in one transaction, leaving any already published alone."""
    if not records:
        return
    statement = insert(grading_rules).values(
        [
            {
                "version": record.version,
                "company": record.company,
                "effective_from": record.effective_from,
                "source": record.source,
                "verified_on": record.verified_on,
                "rules": dict(record.rules),
            }
            for record in records
        ]
    )
    async with engine.begin() as connection:
        await connection.execute(statement.on_conflict_do_nothing(index_elements=["version"]))


async def seed() -> tuple[GradingRules, ...]:
    """Load the published records and apply them to the configured database."""
    records = load_grading_rules()
    engine = create_engine()
    try:
        await apply_grading_rules(records, engine)
    finally:
        await engine.dispose()
    return records


def main() -> int:
    """Console-script entry point (`uv run tcg-seed-grading-rules`)."""
    argparse.ArgumentParser(description=__doc__, add_help=True).parse_args()

    configure_logging(get_settings())

    try:
        records = asyncio.run(seed())
    except GradingSeedError as error:
        logger.error("grading rules seed rejected: %s", error)
        return 1

    logger.info(
        "grading rules seeded: %d versions (%s)",
        len(records),
        ", ".join(record.version for record in records),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
