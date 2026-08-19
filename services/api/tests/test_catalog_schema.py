"""Integration tests for the catalog schema as PostgreSQL actually built it.

`test_catalog_tables.py` proves the schema was *declared* correctly. This proves
the migration built what was declared, and — more usefully — that the guarantees
only a real database can demonstrate hold: the composite foreign key rejects a
card that disagrees with its set, a variant-less card cannot be inserted twice,
Japanese text survives byte for byte, and the prefix index is genuinely usable
for `LIKE 'x%'`, which depends on the collation.

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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

# The whole schema, not just the catalog's part of it: the drift guard below is
# this repository's only check that `tables.py` and the migrations agree, and it
# must therefore see every domain. Importing the analysis module is what puts its
# three tables on this `MetaData` (see `tcg_api.tables`).
from tcg_api.analysis import tables as _analysis_tables  # noqa: F401
from tcg_api.catalog.tables import (
    card_database_versions,
    card_external_ids,
    cards,
    sets,
)
from tcg_api.tables import metadata

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to inspect",
    ),
]

CATALOG_TABLES = ("sets", "cards", "card_external_ids", "card_database_versions")
HARNESS_TABLE = "migration_harness_check"


def alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"


def run_sync(work: Callable[[Connection], Any]) -> Any:
    """Run one synchronous callable against a fresh connection."""

    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(work)
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def query(statement: sa.Executable, parameters: Any = None) -> list[Any]:
    def work(connection: Connection) -> list[Any]:
        return list(connection.execute(statement, parameters))

    return run_sync(work)


def write(statements: list[tuple[sa.Executable, Any]]) -> None:
    """Execute `statements` in one transaction, letting failures propagate."""

    def work(connection: Connection) -> None:
        with connection.begin():
            for statement, parameters in statements:
                connection.execute(statement, parameters)

    run_sync(work)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """Every test in this module reads the schema at `head`.

    `test_migrations.py` deliberately leaves the database at `base`, and pytest
    makes no promise about which module runs first.
    """
    alembic("upgrade", "head")


@pytest.fixture(autouse=True)
def empty_tables():
    def truncate(connection: Connection) -> None:
        with connection.begin():
            # TRUNCATE bypasses row-level triggers, which is the only reason
            # `card_database_versions` can be emptied at all: its trigger
            # refuses DELETE. RESTART IDENTITY also resets `ordinal`, so each
            # test reasons about publication order from 1.
            # CASCADE now also reaches `analyses` and `images`, through
            # `analyses.card_id` — harmless here, and the reason the analysis
            # schema's own fixture names those tables explicitly rather than
            # relying on this one having run.
            connection.execute(
                sa.text(
                    "TRUNCATE card_external_ids, cards, sets, card_database_versions "
                    "RESTART IDENTITY CASCADE"
                )
            )

    run_sync(truncate)
    yield
    run_sync(truncate)


# ---------------------------------------------------------------------------
# Fixture data. Built here rather than loaded from `database/seeds` so that a
# schema test fails for schema reasons and never because a seed changed.
# ---------------------------------------------------------------------------
BASE_SET_ID = uuid.UUID("11111111-1111-5111-8111-111111111111")
JAPANESE_SET_ID = uuid.UUID("22222222-2222-5222-8222-222222222222")


def insert_set(identifier: uuid.UUID, *, language: str = "en", set_code: str = "BS") -> None:
    write(
        [
            (
                sa.insert(sets),
                {
                    "id": identifier,
                    "game": "pokemon",
                    "language": language,
                    "set_code": set_code,
                    "name": f"Set {set_code}",
                },
            )
        ]
    )


def card_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "game": "pokemon",
        "language": "en",
        "set_id": BASE_SET_ID,
        "card_number": "4/102",
        "name": "Charizard",
        "rarity": None,
        "variant": None,
    }
    values.update(overrides)
    return values


def insert_card(**overrides: Any) -> uuid.UUID:
    values = card_values(**overrides)
    write([(sa.insert(cards), values)])
    return values["id"]


# ---------------------------------------------------------------------------
# The migration built what the code declares
# ---------------------------------------------------------------------------
def test_the_migration_and_the_declared_tables_agree() -> None:
    """If this fails, `tables.py` and the migration have drifted apart.

    Alembic's own comparison, which is also what `--autogenerate` would use, so
    a green run here means the next autogenerated revision starts from nothing.
    It covers **every** declared table, not only the catalog's — the `MetaData`
    is shared, and this is the only place the comparison is made.

    One thing it does not cover: Alembic compares a check constraint's name and
    not its text, so an `IN` list that had drifted would pass here. The
    parametrised insert tests in `test_analysis_schema.py` are what catch that.
    """

    def work(connection: Connection) -> list[Any]:
        context = MigrationContext.configure(connection)
        return compare_metadata(context, metadata)

    assert run_sync(work) == []


@pytest.mark.parametrize("table", CATALOG_TABLES)
def test_the_catalog_table_exists(table: str) -> None:
    assert query(sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"})[0][
        0
    ]


def test_the_first_domain_migration_drops_the_harness_table() -> None:
    """The baseline said so in its own COMMENT ON TABLE. This is where it happens."""
    found = query(
        sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{HARNESS_TABLE}"}
    )
    assert found[0][0] is False


def test_no_postgresql_extension_is_required() -> None:
    """Spec §8 keeps the platform open between ordinary PostgreSQL and Supabase."""
    installed = {row[0] for row in query(sa.text("SELECT extname FROM pg_extension"))}
    assert installed <= {"plpgsql"}


# ---------------------------------------------------------------------------
# Collation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("sets", "set_code"),
        ("sets", "name"),
        ("cards", "card_number"),
        ("cards", "card_number_key"),
        ("cards", "name"),
        ("cards", "variant"),
        ("card_external_ids", "external_id"),
    ],
)
def test_printed_text_is_collated_c_in_the_database(table: str, column: str) -> None:
    """Verified, not assumed — the issue asks for exactly this.

    The local database is initialised `--locale=C` and CI's is not, so a column
    that inherited the server's collation would sort differently in the two.
    """
    collation = query(
        sa.text(
            "SELECT collation_name FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    assert collation[0][0] == "C"


# ---------------------------------------------------------------------------
# A card cannot disagree with its own set
# ---------------------------------------------------------------------------
def test_a_card_may_not_contradict_its_sets_language() -> None:
    insert_set(BASE_SET_ID, language="en")

    with pytest.raises(IntegrityError, match="fk_cards_set_id_game_language_sets"):
        insert_card(language="ja")


def test_a_card_may_not_contradict_its_sets_game() -> None:
    insert_set(BASE_SET_ID)

    with pytest.raises(IntegrityError, match="fk_cards_set_id_game_language_sets"):
        insert_card(game="one-piece")


def test_a_set_may_not_be_deleted_while_cards_reference_it() -> None:
    insert_set(BASE_SET_ID)
    insert_card()

    with pytest.raises(IntegrityError, match="fk_cards_set_id_game_language_sets"):
        write([(sa.delete(sets).where(sets.c.id == BASE_SET_ID), None)])


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------
def test_a_variantless_card_cannot_be_inserted_twice() -> None:
    """PostgreSQL's default NULL handling would allow this; NULLS NOT DISTINCT does not."""
    insert_set(BASE_SET_ID)
    insert_card(variant=None)

    with pytest.raises(IntegrityError, match="uq_cards_set_id_card_number_variant"):
        insert_card(variant=None)


def test_the_same_card_number_may_carry_several_variants() -> None:
    """Holo, reverse holo and 1st edition are different cards, and price differently."""
    insert_set(BASE_SET_ID)
    for variant in ("1st-edition-holo", "shadowless-holo", "unlimited-holo"):
        insert_card(variant=variant)

    assert query(sa.select(sa.func.count()).select_from(cards))[0][0] == 3


def test_a_set_code_is_unique_per_game_and_language_but_not_across_them() -> None:
    """Japanese sets are their own sets, so a code may repeat across languages."""
    insert_set(BASE_SET_ID, language="en", set_code="SV3")
    insert_set(JAPANESE_SET_ID, language="ja", set_code="SV3")

    with pytest.raises(IntegrityError, match="uq_sets_game_language_set_code"):
        insert_set(uuid.uuid4(), language="en", set_code="SV3")


# ---------------------------------------------------------------------------
# card_external_ids — spec §10's reason for a third table
# ---------------------------------------------------------------------------
def external_id_values(card_id: uuid.UUID, provider: str, external: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "card_id": card_id,
        "provider": provider,
        "external_id": external,
    }


def test_one_canonical_card_may_carry_identifiers_from_two_providers() -> None:
    insert_set(BASE_SET_ID)
    card_id = insert_card()

    write(
        [
            (sa.insert(card_external_ids), external_id_values(card_id, "manual", "bs-4")),
            (sa.insert(card_external_ids), external_id_values(card_id, "tcgdex", "base1-4")),
        ]
    )

    providers = query(
        sa.select(card_external_ids.c.provider).where(card_external_ids.c.card_id == card_id)
    )
    assert {row[0] for row in providers} == {"manual", "tcgdex"}


def test_one_provider_identifier_may_name_two_of_our_cards() -> None:
    """A source that does not split printing variants issues one id for both."""
    insert_set(BASE_SET_ID)
    holo = insert_card(variant="unlimited-holo")
    reverse = insert_card(variant="reverse-holo")

    write(
        [
            (sa.insert(card_external_ids), external_id_values(holo, "tcgdex", "base1-4")),
            (sa.insert(card_external_ids), external_id_values(reverse, "tcgdex", "base1-4")),
        ]
    )

    assert query(sa.select(sa.func.count()).select_from(card_external_ids))[0][0] == 2


def test_the_same_provider_identifier_cannot_be_recorded_twice_for_one_card() -> None:
    insert_set(BASE_SET_ID)
    card_id = insert_card()
    write([(sa.insert(card_external_ids), external_id_values(card_id, "manual", "bs-4"))])

    with pytest.raises(IntegrityError, match="uq_card_external_ids_card_id_provider_external_id"):
        write([(sa.insert(card_external_ids), external_id_values(card_id, "manual", "bs-4"))])


def test_deleting_a_card_takes_its_external_identifiers_with_it() -> None:
    insert_set(BASE_SET_ID)
    card_id = insert_card()
    write([(sa.insert(card_external_ids), external_id_values(card_id, "manual", "bs-4"))])

    write([(sa.delete(cards).where(cards.c.id == card_id), None)])

    assert query(sa.select(sa.func.count()).select_from(card_external_ids))[0][0] == 0


# ---------------------------------------------------------------------------
# Japanese text
# ---------------------------------------------------------------------------
def test_a_japanese_card_round_trips_byte_for_byte() -> None:
    insert_set(JAPANESE_SET_ID, language="ja", set_code="SV2a")
    insert_card(
        language="ja",
        set_id=JAPANESE_SET_ID,
        card_number="006/165",
        name="リザードン",
    )

    row = query(
        sa.select(cards.c.name, sa.func.octet_length(cards.c.name)).where(cards.c.language == "ja")
    )[0]

    assert row[0] == "リザードン"
    # Five characters, three UTF-8 bytes each: proof the encoding survived.
    assert row[1] == 15


def test_a_japanese_name_is_searchable_by_prefix_and_by_fragment() -> None:
    insert_set(JAPANESE_SET_ID, language="ja", set_code="SV2a")
    insert_card(language="ja", set_id=JAPANESE_SET_ID, card_number="006/165", name="リザードン")
    insert_card(language="ja", set_id=JAPANESE_SET_ID, card_number="201/165", name="リザードンex")

    prefix = query(sa.select(cards.c.name).where(cards.c.name.like("リザードン%")))
    fragment = query(sa.select(cards.c.name).where(cards.c.name.like("%ザード%")))

    assert len(prefix) == 2
    assert len(fragment) == 2


# ---------------------------------------------------------------------------
# The search contract's index requirements
# ---------------------------------------------------------------------------
def test_a_typed_card_number_matches_the_printed_one() -> None:
    """ "025/165" is printed; "25" is what a user types."""
    insert_set(BASE_SET_ID)
    insert_card(card_number="025/165", name="Pikachu")

    matched = query(
        sa.select(cards.c.card_number, cards.c.card_number_key).where(
            cards.c.card_number_key.like("25%")
        )
    )

    assert matched == [("025/165", "25")]


def test_the_card_number_prefix_index_can_serve_a_like_query() -> None:
    """A B-tree only answers `LIKE 'x%'` as an index range when the collation permits it.

    Under a non-C collation PostgreSQL cannot turn `LIKE '25%'` into bounds, and
    the prefix would become a `Filter` applied after the fact — correct, but a
    scan. Enough rows are inserted, and `ANALYZE` run, for the planner to have
    real statistics: on a three-row table every plan costs the same and the
    choice would say nothing.
    """
    insert_set(BASE_SET_ID)
    write(
        [
            (
                sa.insert(cards),
                [
                    card_values(card_number=f"{number:03d}/300", name=f"Card {number:03d}")
                    for number in range(1, 301)
                ],
            )
        ]
    )

    def analyze(connection: Connection) -> None:
        # Statistics are transactional, so this must commit: rolled back, the
        # planner would still believe the table holds one row.
        with connection.begin():
            connection.execute(sa.text("ANALYZE cards"))

    run_sync(analyze)

    def explain(connection: Connection) -> str:
        with connection.begin():
            rows = connection.execute(
                sa.text(
                    "EXPLAIN SELECT id FROM cards "
                    "WHERE game = 'pokemon' AND language = 'en' "
                    "AND card_number_key LIKE '25%'"
                )
            )
            return "\n".join(row[0] for row in rows)

    plan = run_sync(explain)

    assert "ix_cards_game_language_card_number_key" in plan, plan
    # A range, not a post-filter: this is the property the collation buys.
    assert "card_number_key >=" in plan, plan


# ---------------------------------------------------------------------------
# Spec §57 — the version record is ordered and immutable (#27)
# ---------------------------------------------------------------------------
def version_values(version: str, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "version": version,
        "source": "manual",
        "generated_at": datetime(2026, 8, 18, tzinfo=UTC),
        "set_count": 4,
        "card_count": 22,
        "external_id_count": 23,
    }
    values.update(overrides)
    return values


def publish(version: str, **overrides: Any) -> None:
    write([(sa.insert(card_database_versions), version_values(version, **overrides))])


def test_publication_order_is_assigned_in_the_order_rows_arrive() -> None:
    publish("pokemon-catalog-v0.2.0")
    publish("pokemon-catalog-v0.10.0")

    ordered = query(
        sa.select(card_database_versions.c.version).order_by(card_database_versions.c.ordinal)
    )

    assert [row[0] for row in ordered] == ["pokemon-catalog-v0.2.0", "pokemon-catalog-v0.10.0"]


def test_the_identifier_is_not_the_order() -> None:
    """This is why `ordinal` exists, and the reason is easy to forget.

    Under `COLLATE "C"` 'pokemon-catalog-v0.10.0' sorts *before* 'v0.2.0', so a
    "current version" resolved by ordering on the identifier would answer with
    the older catalog the moment a minor version reached double digits. The
    wrong answer is asserted explicitly, because that is what makes this test
    worth having.
    """
    publish("pokemon-catalog-v0.2.0")
    publish("pokemon-catalog-v0.10.0")

    by_ordinal = query(
        sa.select(card_database_versions.c.version)
        .order_by(card_database_versions.c.ordinal.desc())
        .limit(1)
    )
    by_identifier = query(
        sa.select(card_database_versions.c.version)
        .order_by(card_database_versions.c.version.desc())
        .limit(1)
    )

    assert by_ordinal[0][0] == "pokemon-catalog-v0.10.0"
    assert by_identifier[0][0] == "pokemon-catalog-v0.2.0"


def test_a_writer_cannot_place_itself_in_the_order() -> None:
    """GENERATED ALWAYS: supplying an ordinal takes OVERRIDING SYSTEM VALUE."""
    with pytest.raises(ProgrammingError, match="ordinal"):
        publish("pokemon-catalog-v0.2.0", ordinal=99)


def test_a_published_version_cannot_be_rewritten() -> None:
    publish("pokemon-catalog-v0.2.0")

    with pytest.raises(IntegrityError, match="immutable"):
        write([(sa.text("UPDATE card_database_versions SET source = 'tcgdex'"), None)])


def test_a_published_version_cannot_be_deleted() -> None:
    publish("pokemon-catalog-v0.2.0")

    with pytest.raises(IntegrityError, match="immutable"):
        write([(sa.text("DELETE FROM card_database_versions"), None)])


def test_the_same_identifier_cannot_be_published_twice() -> None:
    """A second run publishes a new version; it never re-publishes an old one."""
    publish("pokemon-catalog-v0.2.0")

    with pytest.raises(IntegrityError, match="uq_card_database_versions_version"):
        publish("pokemon-catalog-v0.2.0")


def test_a_negative_record_count_is_refused() -> None:
    with pytest.raises(IntegrityError, match="counts_are_not_negative"):
        publish("pokemon-catalog-v0.2.0", card_count=-1)


def test_when_the_row_was_written_is_assigned_by_the_server() -> None:
    publish("pokemon-catalog-v0.2.0")

    written = query(sa.select(card_database_versions.c.created_at))

    assert written[0][0] is not None


def test_a_version_outlives_the_cards_it_counted() -> None:
    """No foreign key into `cards`, demonstrated rather than asserted.

    A historical analysis needs the version it recorded to still be there after
    the catalog has been replaced under it — which is exactly what a foreign key
    would have prevented.
    """
    insert_set(BASE_SET_ID)
    insert_card()
    publish("pokemon-catalog-v0.2.0")

    write([(sa.text("DELETE FROM cards"), None)])

    surviving = query(sa.select(card_database_versions.c.version))
    assert [row[0] for row in surviving] == ["pokemon-catalog-v0.2.0"]
