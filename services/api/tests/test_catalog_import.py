"""The snapshot format and the import that writes one to PostgreSQL.

Split in two, like `test_catalog_seed.py`. The format tests build a snapshot in
a temporary directory from the recorded TCGdex payloads and need no database.
The applying tests are marked `integration` and need a live PostgreSQL with the
migrations applied:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    uv run alembic upgrade head

None of it touches the network: fetching is a separate phase for exactly that
reason, and what a load consumes is a snapshot on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tcg_api.catalog import import_catalog, tcgdex
from tcg_api.catalog.seed import apply_seed_catalog, load_seed_catalog, seed_card_id
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
from tcg_api.catalog.tables import card_database_versions, card_external_ids, cards, sets
from tcg_domain.catalog_version import CardDatabaseVersion
from tcg_domain.errors import InvalidCatalogRecord
from test_catalog_tcgdex import payload

REPO_ROOT = Path(__file__).resolve().parents[3]

DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to import into",
)

#: The seed fixtures hold this printing of Base Set Charizard under the `manual`
#: provider, and TCGdex describes the same printing. The identifier is derived
#: from `(game, language, set_code, card_number, variant)`, so both sources
#: resolve to this one row — which is the property #26 has to demonstrate.
CHARIZARD = seed_card_id("pokemon", "en", "BS", "4/102", "unlimited-holo")


def tcgdex_records(*card_files: str) -> CatalogRecords:
    """Base Set, and the named recorded cards, as the importer would map them."""
    parent = tcgdex.set_record(payload("set-en-base1.json"), "en")
    all_cards = []
    all_external_ids = []
    for name in card_files:
        mapped, external_ids = tcgdex.card_records(payload(name), parent)
        all_cards.extend(mapped)
        all_external_ids.extend(external_ids)
    return CatalogRecords(
        sets=(parent,), cards=tuple(all_cards), external_ids=tuple(all_external_ids)
    )


def manifest(records: CatalogRecords, version: str, directory: Path) -> CardDatabaseVersion:
    set_count, card_count, external_id_count = records.counts
    return CardDatabaseVersion(
        version=version,
        source=tcgdex.PROVIDER,
        source_license=tcgdex.LICENSE,
        source_revision="0123456789abcdef0123456789abcdef01234567",
        generated_at=datetime(2026, 8, 19, tzinfo=UTC),
        set_count=set_count,
        card_count=card_count,
        external_id_count=external_id_count,
        metadata={
            "api_base_url": tcgdex.DEFAULT_API_BASE_URL,
            DIGEST_KEY: records_digest(directory),
        },
    )


def snapshot(directory: Path, *, version: str, card_files: tuple[str, ...] = ()) -> CatalogRecords:
    """Write a complete snapshot — records then manifest — and return the records."""
    records = tcgdex_records(*(card_files or ("card-en-base1-4.json",)))
    write_records(records, directory)
    write_manifest(manifest(records, version, directory), directory)
    return records


# ---------------------------------------------------------------------------
# The format
# ---------------------------------------------------------------------------
def test_records_survive_a_round_trip_through_the_files(tmp_path: Path) -> None:
    written = tcgdex_records("card-en-base1-4.json")
    write_records(written, tmp_path)

    read = read_records(tmp_path)

    assert [record.id for record in read.sets] == [record.id for record in written.sets]
    assert [card.id for card in read.cards] == [card.id for card in written.cards]
    assert [card.variant for card in read.cards] == [card.variant for card in written.cards]
    assert [external.card_id for external in read.external_ids] == [
        external.card_id for external in written.external_ids
    ]


def test_japanese_survives_a_round_trip_without_mojibake(tmp_path: Path) -> None:
    """The whole point of the Japanese half of the catalog."""
    parent = tcgdex.set_record(payload("set-ja-SV2a.json"), "ja")
    mapped, external_ids = tcgdex.card_records(payload("card-ja-SV2a-025.json"), parent)
    write_records(
        CatalogRecords(sets=(parent,), cards=tuple(mapped), external_ids=tuple(external_ids)),
        tmp_path,
    )

    read = read_records(tmp_path)

    assert read.sets[0].name == "ポケモンカード151"
    assert {card.name for card in read.cards} == {"ピカチュウ"}
    # And on the way in, too: a file full of \uXXXX escapes would read back the
    # same but be unreviewable in a diff.
    assert "ポケモンカード151" in (tmp_path / "sets.json").read_text(encoding="utf-8")


def test_a_manifest_survives_a_round_trip(tmp_path: Path) -> None:
    records = tcgdex_records("card-en-base1-4.json")
    write_records(records, tmp_path)
    written = manifest(records, "pokemon-catalog-tcgdex-v0.1.0", tmp_path)
    write_manifest(written, tmp_path)

    read = read_manifest(tmp_path)

    assert read.version == written.version
    assert read.source == "tcgdex"
    assert read.source_license == "MIT"
    assert read.source_revision == written.source_revision
    assert read.generated_at == written.generated_at
    assert (read.set_count, read.card_count, read.external_id_count) == records.counts


def test_a_manifest_naming_a_moving_pointer_is_refused(tmp_path: Path) -> None:
    """Spec §31: versions are explicit and ordered, never `/latest/`."""
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "version": "pokemon-catalog-latest-v1.0.0",
                "source": "tcgdex",
                "generated_at": "2026-08-19T00:00:00+00:00",
                "set_count": 0,
                "card_count": 0,
                "external_id_count": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InvalidCatalogRecord, match="latest"):
        read_manifest(tmp_path)


def test_a_manifest_with_a_naive_timestamp_is_refused(tmp_path: Path) -> None:
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "version": "pokemon-catalog-tcgdex-v0.1.0",
                "source": "tcgdex",
                "generated_at": "2026-08-19T00:00:00",
                "set_count": 0,
                "card_count": 0,
                "external_id_count": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(InvalidCatalogRecord):
        read_manifest(tmp_path)


def test_the_same_records_hash_the_same_twice(tmp_path: Path) -> None:
    """Otherwise the digest recorded in a manifest would mean nothing."""
    records = tcgdex_records("card-en-base1-4.json")

    write_records(records, tmp_path / "first")
    write_records(records, tmp_path / "second")

    assert records_digest(tmp_path / "first") == records_digest(tmp_path / "second")


def test_different_records_hash_differently(tmp_path: Path) -> None:
    write_records(tcgdex_records("card-en-base1-4.json"), tmp_path / "one")
    write_records(tcgdex_records("card-en-base1-4.json", "card-en-base1-58.json"), tmp_path / "two")

    assert records_digest(tmp_path / "one") != records_digest(tmp_path / "two")


def test_a_snapshot_edited_after_it_was_written_is_refused(tmp_path: Path) -> None:
    snapshot(tmp_path, version="pokemon-catalog-tcgdex-v0.1.0")
    (tmp_path / "cards.json").write_text("[]", encoding="utf-8")

    with pytest.raises(SnapshotError, match="edited since"):
        verify_digest(read_manifest(tmp_path), tmp_path)


def test_a_manifest_that_records_no_digest_is_accepted(tmp_path: Path) -> None:
    """`database/seeds/catalog/` predates the key and is not thereby corrupt."""
    records = tcgdex_records("card-en-base1-4.json")
    write_records(records, tmp_path)
    set_count, card_count, external_id_count = records.counts
    write_manifest(
        CardDatabaseVersion(
            version="pokemon-catalog-tcgdex-v0.1.0",
            source="tcgdex",
            generated_at=datetime(2026, 8, 19, tzinfo=UTC),
            set_count=set_count,
            card_count=card_count,
            external_id_count=external_id_count,
        ),
        tmp_path,
    )

    verify_digest(read_manifest(tmp_path), tmp_path)


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
def parse(*argv: str) -> None:
    parser = import_catalog._parser()
    import_catalog._validated(parser, parser.parse_args(list(argv)))


def test_a_fetch_must_name_the_version_it_publishes() -> None:
    """A constant identifier would make two runs one record, and #26 needs two."""
    with pytest.raises(SystemExit):
        parse("--language", "en")


def test_loading_a_snapshot_will_not_take_a_version(tmp_path: Path) -> None:
    """The manifest already carries one, and a second would be a claim about nothing."""
    with pytest.raises(SystemExit):
        parse("--from-snapshot", str(tmp_path), "--version", "pokemon-catalog-tcgdex-v0.1.0")


def test_fetch_only_and_from_snapshot_are_opposite_halves(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse("--from-snapshot", str(tmp_path), "--fetch-only")


def test_a_fetch_naming_a_version_is_accepted() -> None:
    parse("--version", "pokemon-catalog-tcgdex-v0.1.0", "--language", "en", "--set", "base1")


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
def _engine() -> AsyncEngine:
    return create_async_engine(DATABASE_URL or "")


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` leaves the database at `base` and pytest promises no ordering."""
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
def empty_catalog_tables():
    async def truncate() -> None:
        engine = _engine()
        try:
            async with engine.begin() as connection:
                # TRUNCATE, because `card_database_versions` has a trigger that
                # refuses DELETE.
                await connection.execute(
                    sa.text(
                        "TRUNCATE card_external_ids, cards, sets, card_database_versions "
                        "RESTART IDENTITY CASCADE"
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(truncate())
    yield
    asyncio.run(truncate())


def counted() -> dict[str, int]:
    async def scenario() -> dict[str, int]:
        engine = _engine()
        try:
            async with engine.connect() as connection:
                return {
                    name: (
                        await connection.execute(sa.select(sa.func.count()).select_from(table))
                    ).scalar_one()
                    for name, table in (
                        ("sets", sets),
                        ("cards", cards),
                        ("external_ids", card_external_ids),
                        ("versions", card_database_versions),
                    )
                }
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def apply_snapshot(directory: Path) -> None:
    async def scenario() -> None:
        engine = _engine()
        try:
            await apply_records(read_records(directory), read_manifest(directory), engine)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_importing_the_same_snapshot_twice_writes_the_same_rows(tmp_path: Path) -> None:
    """ "Idempotency matters more than speed" — issue #26."""
    records = snapshot(tmp_path, version="pokemon-catalog-tcgdex-v0.1.0")

    apply_snapshot(tmp_path)
    once = counted()
    apply_snapshot(tmp_path)
    twice = counted()

    set_count, card_count, external_id_count = records.counts
    assert once == {
        "sets": set_count,
        "cards": card_count,
        "external_ids": external_id_count,
        "versions": 1,
    }
    assert twice == once


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_two_runs_publish_two_distinct_ordered_versions(tmp_path: Path) -> None:
    """Moved here from #27, which could not assert it before this pipeline existed."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    snapshot(first, version="pokemon-catalog-tcgdex-v0.1.0")
    snapshot(second, version="pokemon-catalog-tcgdex-v0.2.0", card_files=("card-en-base1-58.json",))

    apply_snapshot(first)
    apply_snapshot(second)

    async def published() -> list[tuple[str, int]]:
        engine = _engine()
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(
                    sa.select(
                        card_database_versions.c.version, card_database_versions.c.ordinal
                    ).order_by(card_database_versions.c.ordinal)
                )
                return [(row.version, row.ordinal) for row in rows]
        finally:
            await engine.dispose()

    records = asyncio.run(published())

    assert [version for version, _ in records] == [
        "pokemon-catalog-tcgdex-v0.1.0",
        "pokemon-catalog-tcgdex-v0.2.0",
    ]
    assert records[0][1] < records[1][1]


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_the_import_records_where_the_records_came_from(tmp_path: Path) -> None:
    """ADR 0004: source, licence, upstream revision and counts, in the version record."""
    snapshot(tmp_path, version="pokemon-catalog-tcgdex-v0.1.0")

    apply_snapshot(tmp_path)

    async def published() -> sa.Row[object]:
        engine = _engine()
        try:
            async with engine.connect() as connection:
                return (
                    (await connection.execute(sa.select(card_database_versions))).mappings().one()
                )
        finally:
            await engine.dispose()

    record = asyncio.run(published())

    assert record["source"] == "tcgdex"
    assert record["source_license"] == "MIT"
    assert record["source_revision"] == "0123456789abcdef0123456789abcdef01234567"
    assert record["metadata"][DIGEST_KEY] == records_digest(tmp_path)
    assert (record["set_count"], record["card_count"], record["external_id_count"]) == (1, 4, 4)


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_two_sources_describing_one_printing_become_one_card(tmp_path: Path) -> None:
    """The seed's `manual` Charizard and TCGdex's are the same physical card.

    Both resolve `(pokemon, en, BS, 4/102, unlimited-holo)` to the same derived
    id, so the second import upserts onto the first row rather than adding a
    rival one, and the two providers' identifiers sit beside each other. Spec
    §10 asks for exactly this, and it is why `cards` has no provider column.
    """
    snapshot(tmp_path, version="pokemon-catalog-tcgdex-v0.1.0")

    async def load_both() -> tuple[int, list[tuple[str, str]]]:
        engine = _engine()
        try:
            await apply_seed_catalog(load_seed_catalog(), engine)
            await apply_records(read_records(tmp_path), read_manifest(tmp_path), engine)
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(sa.select(cards).where(cards.c.id == CHARIZARD))
                ).all()
                providers = await connection.execute(
                    sa.select(card_external_ids.c.provider, card_external_ids.c.external_id).where(
                        card_external_ids.c.card_id == CHARIZARD
                    )
                )
                return len(rows), sorted((row.provider, row.external_id) for row in providers)
        finally:
            await engine.dispose()

    row_count, providers = asyncio.run(load_both())

    assert row_count == 1
    assert ("manual", "bs-4-unlimited-holo") in providers
    assert ("tcgdex", "base1-4") in providers
