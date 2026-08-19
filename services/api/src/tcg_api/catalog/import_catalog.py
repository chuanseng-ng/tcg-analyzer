"""Import the canonical card catalog, recording where every record came from.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg

    # Fetch English and Japanese from TCGdex into a snapshot, then load it.
    uv run tcg-import-catalog --version pokemon-catalog-tcgdex-v0.1.0 \\
        --language en --language ja --snapshot .catalog-snapshots/tcgdex

    # Load a snapshot that was fetched earlier. No network.
    uv run tcg-import-catalog --from-snapshot .catalog-snapshots/tcgdex

    # Fetch only. No database.
    uv run tcg-import-catalog --version ... --snapshot ... --fetch-only

**Two phases, because the source has ~36,000 cards and no version stamp.**
Rarity and printing variants come only from TCGdex's per-card endpoint, so a
full import is tens of thousands of requests over the better part of an hour.
Streaming that straight into PostgreSQL would mean an interrupted run leaves a
half-written catalog, a re-run is never the same twice, and no test can exercise
the loader without a network. Writing a snapshot first fixes all three: the
snapshot is a reviewable artifact, it has a sha256 digest that a later load
verifies, and `--from-snapshot` replays it exactly.

**A version identifier is required and is never a constant.** Spec §31 wants
explicit, ordered versions rather than a moving pointer, and the record is
immutable once published — `card_database_versions` has a trigger that refuses
UPDATE. Two imports are two versions; the rows they write converge, the records
of the runs accumulate.

**Provenance goes in the version record and nowhere else.** #27 built
`card_database_versions` with `source`, `source_license` and `source_revision`
columns for this, and ADR 0004 forbids a competing provenance table. What a run
can honestly claim is recorded there: the source, `MIT`, the upstream commit
when GitHub can be reached for it, when the data was retrieved, the counts it
wrote, and — in `metadata` — the digest of the snapshot it loaded.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tcg_domain.catalog_version import CardDatabaseVersion
from tcg_domain.errors import InvalidCatalogRecord

from tcg_api.catalog import tcgdex
from tcg_api.catalog.snapshot import (
    DIGEST_KEY,
    CatalogRecords,
    SnapshotError,
    apply_records,
    read_manifest,
    read_records,
    records_digest,
    verify_digest,
    write_manifest,
    write_records,
)
from tcg_api.catalog.tcgdex import CatalogImportError
from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.logging import configure_logging

__all__ = ["CatalogImportError", "load", "main", "take_snapshot"]

logger = logging.getLogger(__name__)

#: Where a snapshot lands when nobody says otherwise. Gitignored: a full
#: snapshot is tens of megabytes of a third party's compilation, and
#: `database/seeds/catalog/` remains the catalog that is committed.
DEFAULT_SNAPSHOT_DIR: Final = Path(".catalog-snapshots") / "tcgdex"

DEFAULT_LANGUAGES: Final = ("en", "ja")


async def take_snapshot(
    directory: Path,
    *,
    version: str,
    languages: Sequence[str],
    sets: Sequence[str] | None,
    base_url: str,
    concurrency: int,
    revision: str | None,
    cache: Path | None,
) -> CardDatabaseVersion:
    """Fetch the source into `directory` and write the manifest describing it.

    `generated_at` is when the data was *retrieved*, which is the only thing this
    run knows about when it was made — the column exists precisely so that a
    later load of this snapshot does not claim to have been generated today.
    """
    retrieved_at = datetime.now(UTC)

    async with tcgdex.create_client(base_url) as client:
        fetched = await tcgdex.fetch(
            client, languages, sets=sets, concurrency=concurrency, cache=cache
        )
        resolved = revision if revision is not None else await tcgdex.resolve_revision()

    write_records(fetched.records, directory)
    set_count, card_count, external_id_count = fetched.records.counts

    manifest = CardDatabaseVersion(
        version=version,
        source=tcgdex.PROVIDER,
        source_license=tcgdex.LICENSE,
        source_revision=resolved,
        generated_at=retrieved_at,
        set_count=set_count,
        card_count=card_count,
        external_id_count=external_id_count,
        metadata={
            "api_base_url": base_url,
            "upstream_repository": tcgdex.UPSTREAM_REPOSITORY,
            "languages": list(languages),
            "sets": list(sets) if sets else None,
            "skipped_cards": fetched.skipped,
            # What was left out and why, so the record describes the catalog it
            # actually wrote rather than the source in general.
            "excluded_series": sorted(tcgdex.EXCLUDED_SERIES),
            "excluded_sets": fetched.excluded,
            DIGEST_KEY: records_digest(directory),
        },
    )
    write_manifest(manifest, directory)

    logger.info(
        "snapshot written to %s: %d sets, %d cards, %d external ids, "
        "%d cards skipped, %d sets excluded as non-physical",
        directory,
        set_count,
        card_count,
        external_id_count,
        fetched.skipped,
        fetched.excluded,
    )
    return manifest


async def load(directory: Path) -> tuple[CatalogRecords, CardDatabaseVersion]:
    """Read a snapshot and write it to the configured database, in one transaction."""
    version = read_manifest(directory)
    verify_digest(version, directory)
    records = read_records(directory)

    engine = create_engine()
    try:
        await apply_records(records, version, engine)
    finally:
        await engine.dispose()
    return records, version


async def run(arguments: argparse.Namespace) -> tuple[CatalogRecords, CardDatabaseVersion] | None:
    directory = arguments.from_snapshot or arguments.snapshot

    if arguments.from_snapshot is None:
        await take_snapshot(
            directory,
            version=arguments.version,
            languages=arguments.language or list(DEFAULT_LANGUAGES),
            sets=arguments.set,
            base_url=arguments.api_base_url,
            concurrency=arguments.concurrency,
            revision=arguments.source_revision,
            cache=arguments.cache_dir,
        )

    if arguments.fetch_only:
        return None
    return await load(directory)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--version",
        help=(
            "the catalog version this run publishes, e.g. "
            "pokemon-catalog-tcgdex-v0.1.0. Required unless --from-snapshot, "
            "whose manifest already carries one. Never reused: a published "
            "version is immutable."
        ),
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT_DIR,
        help="where to write the fetched snapshot (default: %(default)s)",
    )
    parser.add_argument(
        "--from-snapshot",
        type=Path,
        help="load this snapshot instead of fetching. No network is used.",
    )
    parser.add_argument(
        "--language",
        action="append",
        metavar="CODE",
        help=f"ISO 639-1 code, repeatable (default: {' '.join(DEFAULT_LANGUAGES)})",
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="ID",
        help="a TCGdex set id, repeatable. Omit to import every set.",
    )
    parser.add_argument(
        "--api-base-url",
        default=tcgdex.DEFAULT_API_BASE_URL,
        help="TCGdex API root, or a self-hosted instance (default: %(default)s)",
    )
    parser.add_argument(
        "--source-revision",
        help=(
            "the upstream commit imported. Resolved from "
            f"{tcgdex.UPSTREAM_REPOSITORY} when omitted."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=tcgdex.DEFAULT_CONCURRENCY,
        help="requests in flight against the source (default: %(default)s)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help=(
            "keep raw card payloads here so an interrupted run resumes rather "
            "than refetching tens of thousands of cards"
        ),
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="write the snapshot and stop. No database is touched.",
    )
    return parser


def _validated(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    if arguments.from_snapshot is not None:
        for flag in ("version", "language", "set", "source_revision", "cache_dir"):
            if getattr(arguments, flag):
                parser.error(
                    f"--{flag.replace('_', '-')} describes a fetch and --from-snapshot does "
                    "not fetch; the snapshot's catalog.json already carries its provenance"
                )
        if arguments.fetch_only:
            parser.error("--fetch-only and --from-snapshot ask for opposite halves of the run")
    elif not arguments.version:
        parser.error(
            "--version is required: every import publishes its own immutable, "
            "ordered card_database_version (spec §31, §57)"
        )
    if arguments.concurrency < 1:
        parser.error("--concurrency must be at least 1")


def main() -> int:
    """Console-script entry point (`uv run tcg-import-catalog`)."""
    parser = _parser()
    arguments = parser.parse_args()
    _validated(parser, arguments)

    configure_logging(get_settings())
    # httpx logs every request at INFO. A full import makes about 36,000 of
    # them, which would bury this module's own progress lines; `tcg_api.catalog.tcgdex`
    # reports per set instead. Raise the level rather than silencing it, so a
    # connection failure still says so.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        result = asyncio.run(run(arguments))
    except (CatalogImportError, SnapshotError, InvalidCatalogRecord) as error:
        logger.error("catalog import rejected: %s", error)
        return 1

    if result is None:
        return 0

    records, version = result
    set_count, card_count, external_id_count = records.counts
    logger.info(
        "catalog imported as %s from %s (%s, revision %s): %d sets, %d cards, %d external ids",
        version.version,
        version.source,
        version.source_license,
        version.source_revision or "unrecorded",
        set_count,
        card_count,
        external_id_count,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
