"""Load the hand-authored catalog seeds into PostgreSQL.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    uv run tcg-seed-catalog

The fixtures under `database/seeds/catalog/` are a deliberately small, verifiable
catalog subset written by hand under the `manual` provider. They are the
catalog a developer gets without a network, and ADR 0004 keeps them as the floor
if the TCGdex position ever has to be withdrawn. They are *not* an authoritative
card database and must never be treated as one; the canonical source is the
decision recorded in `docs/adr/0004-the-canonical-card-catalog-source.md` and
the pipeline that reads it is `tcg_api.catalog.import_catalog`.

Everything about the format, the derived identifiers and the write itself lives
in :mod:`tcg_api.catalog.snapshot`, which the importer shares. What is left here
is the fixtures' own provenance, which is fixed rather than fetched: this
project authored them, so there is no upstream licence and no upstream revision,
and the identifier and timestamp are constants a test can assert against.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from sqlalchemy.ext.asyncio import AsyncEngine
from tcg_domain.catalog import CardId, SetId
from tcg_domain.catalog_version import CardDatabaseVersion
from tcg_domain.errors import InvalidCatalogRecord

from tcg_api.catalog.snapshot import (
    NAMESPACE,
    CatalogRecords,
    SnapshotError,
    apply_records,
    card_id,
    read_records,
    set_id,
)
from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.logging import configure_logging

__all__ = [
    "NAMESPACE",
    "SEED_CATALOG_GENERATED_AT",
    "SEED_CATALOG_SOURCE",
    "SEED_CATALOG_VERSION",
    "SeedCatalog",
    "SeedError",
    "apply_seed_catalog",
    "load_seed_catalog",
    "main",
    "seed_card_id",
    "seed_catalog_version",
    "seed_set_id",
]

logger = logging.getLogger(__name__)

#: Where the fixtures live, relative to a source checkout. `parents[5]` walks
#: `catalog/ -> tcg_api/ -> src/ -> api/ -> services/ -> <repo root>`.
DEFAULT_SEEDS_DIR: Final = Path(__file__).resolve().parents[5] / "database" / "seeds" / "catalog"

#: The catalog version these fixtures publish. Explicit and ordered, never a
#: moving pointer (spec §31).
#:
#: **Bump it when the fixtures change materially.** A published version is
#: immutable — the loader writes it with `ON CONFLICT DO NOTHING` and the table
#: refuses an UPDATE outright — so adding or removing a fixture without bumping
#: this leaves a version record describing content it no longer matches. The
#: loader says so in a warning rather than guessing which of the two was meant.
SEED_CATALOG_VERSION: Final = "pokemon-catalog-seed-v0.0.0"

#: The fixtures' provider key, and the source their version names.
SEED_CATALOG_SOURCE: Final = "manual"

#: Fixed, and deliberately not `now()`. A seed that stamped the clock would make
#: two fresh databases disagree about the same immutable version, and no test
#: could assert what it holds. This is the day the fixtures were written; move it
#: with `SEED_CATALOG_VERSION`, never on its own.
SEED_CATALOG_GENERATED_AT: Final = datetime(2026, 8, 18, tzinfo=UTC)

#: The fixtures' records. An alias rather than a subclass: the seed catalog is
#: an ordinary snapshot, and #26's importer produces the same thing.
SeedCatalog = CatalogRecords

#: What a malformed fixture raises. Named for the seeds because that is where
#: the error message points, and kept as an alias so the importer's failures and
#: the loader's are one class.
SeedError = SnapshotError


def seed_set_id(game: str, language: str, set_code: str) -> SetId:
    """The identifier a seeded set will have, computable without a database."""
    return set_id(game, language, set_code)


def seed_card_id(
    game: str, language: str, set_code: str, card_number: str, variant: str | None
) -> CardId:
    """The identifier a seeded card will have, computable without a database."""
    return card_id(game, language, set_code, card_number, variant)


def load_seed_catalog(directory: Path | None = None) -> SeedCatalog:
    """Read and validate the fixtures. Touches no database.

    Raises:
        SeedError: If a file is missing or malformed, or a card names a set that
            the fixtures do not define.
        InvalidCatalogRecord: If a record is not a legal domain entity.
    """
    return read_records(directory or DEFAULT_SEEDS_DIR, hint="--seeds-dir")


def seed_catalog_version(catalog: SeedCatalog) -> CardDatabaseVersion:
    """The version record these fixtures publish, built without a database.

    The counts come from the parsed fixtures rather than from `count(*)`: they
    describe what *this* run wrote, where a query would also count rows some
    other version wrote. The import pipeline answers it the same way.

    `source_license` is None — the fixtures are this project's own text, and
    writing a licence there would be a lie. `source_revision` likewise: nothing
    was fetched, so there is no upstream revision to repeat.
    """
    set_count, card_count, external_id_count = catalog.counts
    return CardDatabaseVersion(
        version=SEED_CATALOG_VERSION,
        source=SEED_CATALOG_SOURCE,
        generated_at=SEED_CATALOG_GENERATED_AT,
        set_count=set_count,
        card_count=card_count,
        external_id_count=external_id_count,
    )


async def apply_seed_catalog(catalog: SeedCatalog, engine: AsyncEngine) -> None:
    """Write the fixtures to the database, in one transaction, idempotently."""
    await apply_records(
        catalog,
        seed_catalog_version(catalog),
        engine,
        # The importer's fix is a new `--version`; the fixtures' is a constant,
        # so the warning has to name it or the operator has to go looking.
        advice="bump SEED_CATALOG_VERSION",
    )


async def seed(directory: Path | None = None) -> SeedCatalog:
    """Load the fixtures and apply them to the configured database."""
    catalog = load_seed_catalog(directory)
    engine = create_engine()
    try:
        await apply_seed_catalog(catalog, engine)
    finally:
        await engine.dispose()
    return catalog


def main() -> int:
    """Console-script entry point (`uv run tcg-seed-catalog`)."""
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--seeds-dir",
        type=Path,
        default=DEFAULT_SEEDS_DIR,
        help="directory holding sets.json and cards.json (default: %(default)s)",
    )
    arguments = parser.parse_args()

    configure_logging(get_settings())

    try:
        catalog = asyncio.run(seed(arguments.seeds_dir))
    except (SeedError, InvalidCatalogRecord) as error:
        logger.error("catalog seed rejected: %s", error)
        return 1

    set_count, card_count, external_id_count = catalog.counts
    logger.info(
        "catalog seeded as %s: %d sets, %d cards, %d external ids",
        SEED_CATALOG_VERSION,
        set_count,
        card_count,
        external_id_count,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
