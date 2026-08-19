"""Tests for the catalog seed fixtures and their loader.

Split in two. The loading tests read `database/seeds/catalog/` and need no
database at all — the fixtures are validated by constructing domain entities, so
a malformed record fails long before any SQL is emitted. The applying tests are
marked `integration` and need a live PostgreSQL with the migrations applied:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    uv run alembic upgrade head
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tcg_api.catalog.seed import (
    DEFAULT_SEEDS_DIR,
    SEED_CATALOG_GENERATED_AT,
    SEED_CATALOG_SOURCE,
    SEED_CATALOG_VERSION,
    SeedCatalog,
    SeedError,
    apply_seed_catalog,
    load_seed_catalog,
    seed_card_id,
    seed_catalog_version,
    seed_set_id,
)
from tcg_api.catalog.tables import card_database_versions, card_external_ids, cards, sets
from tcg_domain.errors import InvalidCatalogRecord

DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to seed",
)


@pytest.fixture(scope="module")
def catalog() -> SeedCatalog:
    return load_seed_catalog()


# ---------------------------------------------------------------------------
# The fixtures themselves
# ---------------------------------------------------------------------------
def test_the_shipped_fixtures_load_and_validate(catalog: SeedCatalog) -> None:
    """Constructing the domain entities *is* the validation."""
    assert len(catalog.sets) >= 4
    assert len(catalog.cards) >= 20
    assert len(catalog.external_ids) >= len(catalog.cards)


def test_the_fixtures_cover_both_languages_v1_ships(catalog: SeedCatalog) -> None:
    assert {record.language for record in catalog.sets} == {"en", "ja"}


def test_a_japanese_card_name_survives_the_round_trip_through_json(
    catalog: SeedCatalog,
) -> None:
    """Non-Latin text must not be mangled by encoding, escaping or normalisation."""
    japanese = [card for card in catalog.cards if card.language == "ja"]
    assert japanese

    charizard = next(card for card in japanese if card.name == "リザードン")
    assert charizard.set.name == "ポケモンカード151"
    assert charizard.card_number == "006/165"


def test_one_card_carries_a_variant_trio(catalog: SeedCatalog) -> None:
    """Variant disambiguation is only testable if something needs disambiguating.

    Base Set Charizard is the canonical example: 1st edition, shadowless and
    unlimited are the same printed card and are worth wildly different amounts.
    """
    printings = [
        card for card in catalog.cards if card.set.set_code == "BS" and card.card_number == "4/102"
    ]

    assert {card.variant for card in printings} == {
        "1st-edition-holo",
        "shadowless-holo",
        "unlimited-holo",
    }
    # Different rows, because they are different cards economically.
    assert len({card.id for card in printings}) == 3


def test_the_fixtures_include_a_reverse_holo_printing(catalog: SeedCatalog) -> None:
    """Reverse holo post-dates Base Set, so it appears on a modern card instead."""
    reverse = [card for card in catalog.cards if card.variant == "reverse-holo"]

    assert reverse
    assert all(card.set.set_code == "MEW" for card in reverse)


def test_some_cards_have_no_variant_at_all(catalog: SeedCatalog) -> None:
    """These are what `UNIQUE NULLS NOT DISTINCT` exists to protect."""
    assert any(card.variant is None for card in catalog.cards)


def test_one_canonical_card_carries_two_providers(catalog: SeedCatalog) -> None:
    """Spec §10's reason for a separate table, exercised by shipped data."""
    by_card: Counter[object] = Counter(record.card_id for record in catalog.external_ids)
    shared = [card_id for card_id, count in by_card.items() if count > 1]
    assert shared

    providers = {record.provider for record in catalog.external_ids if record.card_id == shared[0]}
    assert len(providers) > 1
    assert "manual" in providers


def test_every_card_has_at_least_one_manual_identifier(catalog: SeedCatalog) -> None:
    manual = {record.card_id for record in catalog.external_ids if record.provider == "manual"}
    assert manual == {card.id for card in catalog.cards}


def test_no_fixture_carries_a_card_image(catalog: SeedCatalog) -> None:
    """ADR 0004: the only card images this product shows are the user's uploads."""
    raw = json.loads((DEFAULT_SEEDS_DIR / "cards.json").read_text(encoding="utf-8"))

    for entry in raw:
        assert "image_front" not in entry
        assert "image_back" not in entry


def test_the_fixtures_name_no_game_but_pokemon_yet_hard_code_none(
    catalog: SeedCatalog,
) -> None:
    """V1 ships Pokémon; `game` is still a field the fixtures set, not a constant."""
    assert {record.game for record in catalog.sets} == {"pokemon"}
    raw = json.loads((DEFAULT_SEEDS_DIR / "sets.json").read_text(encoding="utf-8"))
    assert all("game" in entry for entry in raw)


# ---------------------------------------------------------------------------
# Derived identifiers
# ---------------------------------------------------------------------------
def test_identifiers_are_derived_so_they_can_be_predicted(catalog: SeedCatalog) -> None:
    base_set = next(record for record in catalog.sets if record.set_code == "BS")
    assert base_set.id == seed_set_id("pokemon", "en", "BS")

    charizard = next(
        card
        for card in catalog.cards
        if card.set.set_code == "BS" and card.variant == "1st-edition-holo"
    )
    assert charizard.id == seed_card_id("pokemon", "en", "BS", "4/102", "1st-edition-holo")


def test_loading_twice_produces_the_same_identifiers() -> None:
    first = load_seed_catalog()
    second = load_seed_catalog()

    assert [card.id for card in first.cards] == [card.id for card in second.cards]


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------
def _write(directory: Path, sets_json: object, cards_json: object) -> Path:
    (directory / "sets.json").write_text(json.dumps(sets_json), encoding="utf-8")
    (directory / "cards.json").write_text(json.dumps(cards_json), encoding="utf-8")
    return directory


BASE_SET = {
    "game": "pokemon",
    "language": "en",
    "set_code": "BS",
    "name": "Base Set",
}


def test_a_missing_fixture_directory_names_the_flag_that_fixes_it(tmp_path: Path) -> None:
    with pytest.raises(SeedError, match="--seeds-dir"):
        load_seed_catalog(tmp_path / "absent")


def test_a_card_naming_an_unknown_set_is_rejected(tmp_path: Path) -> None:
    directory = _write(
        tmp_path,
        [BASE_SET],
        [{"set": "pokemon/en/NOPE", "card_number": "1/102", "name": "Alakazam"}],
    )

    with pytest.raises(SeedError, match="pokemon/en/NOPE"):
        load_seed_catalog(directory)


def test_a_repeated_card_is_rejected_rather_than_silently_collapsed(tmp_path: Path) -> None:
    """The derived id would upsert one row onto the other and hide the mistake."""
    card = {"set": "pokemon/en/BS", "card_number": "4/102", "name": "Charizard"}
    directory = _write(tmp_path, [BASE_SET], [card, dict(card)])

    with pytest.raises(SeedError, match="appears twice"):
        load_seed_catalog(directory)


def test_a_malformed_record_fails_with_the_domains_own_error(tmp_path: Path) -> None:
    directory = _write(tmp_path, [BASE_SET | {"language": "english"}], [])

    with pytest.raises(InvalidCatalogRecord, match="ISO 639-1"):
        load_seed_catalog(directory)


def test_a_missing_required_field_names_the_file_and_the_field(tmp_path: Path) -> None:
    directory = _write(tmp_path, [{k: v for k, v in BASE_SET.items() if k != "name"}], [])

    with pytest.raises(SeedError, match=r"sets.json\[0\]: missing required field 'name'"):
        load_seed_catalog(directory)


# ---------------------------------------------------------------------------
# Applying the catalog — needs PostgreSQL
# ---------------------------------------------------------------------------
def _engine() -> AsyncEngine:
    return create_async_engine(DATABASE_URL or "")


def _apply_and_count(runs: int) -> tuple[dict[str, int], dict[str, object]]:
    """Seed `runs` times, then return row counts and one card's current row."""

    async def scenario() -> tuple[dict[str, int], dict[str, object]]:
        catalog = load_seed_catalog()
        engine = _engine()
        try:
            for _ in range(runs):
                await apply_seed_catalog(catalog, engine)

            async with engine.connect() as connection:
                counts = {
                    table.name: await connection.scalar(
                        sa.select(sa.func.count()).select_from(table)
                    )
                    for table in (sets, cards, card_external_ids)
                }
                charizard = (
                    (
                        await connection.execute(
                            sa.select(cards).where(
                                cards.c.id
                                == seed_card_id("pokemon", "en", "BS", "4/102", "unlimited-holo")
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            return counts, dict(charizard)
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


@pytest.fixture
def empty_catalog_tables():
    async def truncate() -> None:
        engine = _engine()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    # `card_database_versions` joins the list because the
                    # loader now publishes one, and TRUNCATE is the only way
                    # to empty it: its trigger refuses DELETE.
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


@pytest.mark.integration
@requires_postgres
@pytest.mark.parametrize("case", [1, 2, 3], ids=["once", "twice", "three-times"])
@pytest.mark.usefixtures("empty_catalog_tables")
def test_seeding_is_idempotent(case: int) -> None:
    """ "Seeds must be idempotent" — `database/seeds/README.md`."""
    counts, _ = _apply_and_count(case)
    catalog = load_seed_catalog()

    assert counts == {
        "sets": len(catalog.sets),
        "cards": len(catalog.cards),
        "card_external_ids": len(catalog.external_ids),
    }


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_seeding_populates_the_generated_card_number_key() -> None:
    """A user types "25"; the card is printed "025/165"."""
    _, charizard = _apply_and_count(1)
    assert charizard["card_number"] == "4/102"
    assert charizard["card_number_key"] == "4"

    async def lookup() -> list[str]:
        engine = _engine()
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(
                    sa.select(cards.c.card_number)
                    .where(cards.c.card_number_key.like("25%"))
                    .where(cards.c.language == "ja")
                )
                return [row[0] for row in rows]
        finally:
            await engine.dispose()

    assert asyncio.run(lookup()) == ["025/165"]


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_a_japanese_name_round_trips_through_the_database() -> None:
    async def lookup() -> list[str]:
        catalog = load_seed_catalog()
        engine = _engine()
        try:
            await apply_seed_catalog(catalog, engine)
            async with engine.connect() as connection:
                rows = await connection.execute(
                    sa.select(cards.c.name)
                    .where(cards.c.name == "リザードンex")
                    .order_by(cards.c.name)
                )
                return [row[0] for row in rows]
        finally:
            await engine.dispose()

    names = asyncio.run(lookup())
    assert names == ["リザードンex", "リザードンex"]


# ---------------------------------------------------------------------------
# The catalog version the fixtures publish (#27)
# ---------------------------------------------------------------------------
def test_the_seed_publishes_an_explicit_ordered_version(catalog: SeedCatalog) -> None:
    """Constructing the entity is the assertion: the grammar lives in the domain.

    Spec §31 forbids a version that names a moving target, and the identifier
    an analysis records has to survive being looked up years later.
    """
    published = seed_catalog_version(catalog)

    assert published.version == "pokemon-catalog-seed-v0.0.0"
    assert published.source == SEED_CATALOG_SOURCE == "manual"


def test_the_seed_version_counts_come_from_the_fixtures(catalog: SeedCatalog) -> None:
    """Not from `count(*)`, which would also count rows another version wrote."""
    published = seed_catalog_version(catalog)

    assert published.set_count == len(catalog.sets)
    assert published.card_count == len(catalog.cards)
    assert published.external_id_count == len(catalog.external_ids)


def test_the_seed_version_carries_no_upstream_licence(catalog: SeedCatalog) -> None:
    """The fixtures are this project's own text; a licence there would be a lie."""
    published = seed_catalog_version(catalog)

    assert published.source_license is None
    assert published.source_revision is None


def test_the_seed_version_is_generated_at_a_fixed_instant(catalog: SeedCatalog) -> None:
    """`now()` would make two fresh databases disagree about one immutable version."""
    assert seed_catalog_version(catalog).generated_at == SEED_CATALOG_GENERATED_AT
    assert SEED_CATALOG_GENERATED_AT.tzinfo is not None


def _published_versions() -> list[dict[str, object]]:
    async def scenario() -> list[dict[str, object]]:
        engine = _engine()
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(
                    sa.select(card_database_versions).order_by(card_database_versions.c.ordinal)
                )
                return [dict(row) for row in rows.mappings()]
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_seeding_registers_the_catalog_version() -> None:
    _apply_and_count(1)
    catalog = load_seed_catalog()

    published = _published_versions()

    assert len(published) == 1
    assert published[0]["version"] == SEED_CATALOG_VERSION
    assert published[0]["source"] == "manual"
    assert published[0]["source_license"] is None
    assert published[0]["set_count"] == len(catalog.sets)
    assert published[0]["card_count"] == len(catalog.cards)
    assert published[0]["external_id_count"] == len(catalog.external_ids)


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_seeding_twice_leaves_one_immutable_version() -> None:
    """`ON CONFLICT DO NOTHING`, reversing the upsert policy the loader uses
    for catalog content. A published version is never rewritten — and the
    table's trigger would refuse the UPDATE `DO UPDATE` implies anyway."""
    _apply_and_count(2)

    published = _published_versions()

    assert len(published) == 1
    assert published[0]["ordinal"] == 1


@pytest.mark.integration
@requires_postgres
@pytest.mark.usefixtures("empty_catalog_tables")
def test_reseeding_changed_fixtures_without_bumping_the_version_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hole `ON CONFLICT DO NOTHING` opens, and the warning that closes it.

    The catalog rows converge on the fixtures while the version record keeps
    describing the ones it was published with. Only the operator can say whether
    that is a correction or a new version, so the loader says so rather than
    guessing.
    """
    catalog = load_seed_catalog()
    _apply_and_count(1)

    shortened = SeedCatalog(
        sets=catalog.sets,
        cards=catalog.cards[:-1],
        external_ids=[
            record for record in catalog.external_ids if record.card_id != catalog.cards[-1].id
        ],
    )

    async def reapply() -> None:
        engine = _engine()
        try:
            await apply_seed_catalog(shortened, engine)
        finally:
            await engine.dispose()

    with caplog.at_level("WARNING", logger="tcg_api.catalog.seed"):
        asyncio.run(reapply())

    assert "SEED_CATALOG_VERSION" in caplog.text
    assert SEED_CATALOG_VERSION in caplog.text
    # The published record is untouched: it still describes what it described.
    assert _published_versions()[0]["card_count"] == len(catalog.cards)
