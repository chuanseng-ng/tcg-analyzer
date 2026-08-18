"""Unit tests for the catalog table definitions.

Every test here runs without PostgreSQL: it inspects the `MetaData` and, where
the point is the SQL, the DDL SQLAlchemy compiles for the PostgreSQL dialect.
`test_catalog_schema.py` proves the same properties hold in a real database
after the migration has run; these prove they were declared on purpose.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_api.catalog.tables import card_external_ids, cards, metadata, sets

SPEC_TABLES = ("sets", "cards", "card_external_ids")


def ddl(table: sa.Table) -> str:
    return str(sa.schema.CreateTable(table).compile(dialect=postgresql.dialect()))


# ---------------------------------------------------------------------------
# Spec §10 — the three tables and their columns
# ---------------------------------------------------------------------------
def test_the_catalog_declares_exactly_the_three_tables_of_section_10() -> None:
    assert set(metadata.tables) == set(SPEC_TABLES)


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (sets, {"id", "game", "language", "set_code", "name", "release_date", "metadata"}),
        (
            cards,
            {
                "id",
                "game",
                "language",
                "set_id",
                "card_number",
                "card_number_key",
                "name",
                "rarity",
                "variant",
                "metadata",
                "image_front",
                "image_back",
                "created_at",
                "updated_at",
            },
        ),
        (
            card_external_ids,
            {"id", "card_id", "provider", "external_id", "metadata", "created_at"},
        ),
    ],
    ids=SPEC_TABLES,
)
def test_columns_match_the_specification(table: sa.Table, expected: set[str]) -> None:
    assert set(table.columns.keys()) == expected


# ---------------------------------------------------------------------------
# Provider independence (spec §2.4, §10)
# ---------------------------------------------------------------------------
def test_no_provider_column_lives_outside_card_external_ids() -> None:
    """A provider column on `cards` would make one external database authoritative."""
    for table in (sets, cards):
        offending = [column.name for column in table.columns if "provider" in column.name]
        assert offending == [], f"{table.name} names a provider in {offending}"

    assert "provider" in card_external_ids.columns


def test_one_external_identifier_may_name_several_cards() -> None:
    """Deliberately not unique — see the comment on the index in `tables.py`.

    A provider that does not split printing variants issues one identifier for
    what this catalog holds as a holo row and a reverse-holo row.
    """
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in card_external_ids.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("provider", "external_id") not in unique_column_sets

    index = next(
        index
        for index in card_external_ids.indexes
        if index.name == "ix_card_external_ids_provider_external_id"
    )
    assert index.unique is False


# ---------------------------------------------------------------------------
# TCG-agnosticism (spec §73)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("table", [sets, cards, card_external_ids], ids=SPEC_TABLES)
def test_nothing_constrains_the_schema_to_one_game(table: sa.Table) -> None:
    """`game` is a column of data, so a second TCG is rows rather than a migration.

    `CreateTable` compiles columns, constraints and defaults but not the
    `COMMENT ON` statements, so a comment that says "'pokemon' in V1" — which is
    documentation — does not make this assertion fail, and a CHECK or a default
    that pinned the game would.
    """
    assert "pokemon" not in ddl(table).lower()
    assert [
        constraint for constraint in table.constraints if isinstance(constraint, sa.CheckConstraint)
    ] == []


def test_game_and_language_are_plain_required_text() -> None:
    for table in (sets, cards):
        for name in ("game", "language"):
            column = table.columns[name]
            assert isinstance(column.type, sa.Text)
            assert column.nullable is False


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------
def test_a_set_is_unique_by_game_language_and_set_code() -> None:
    assert "UNIQUE (game, language, set_code)" in ddl(sets)


def test_a_card_is_unique_by_set_number_and_variant_including_null_variants() -> None:
    """Without NULLS NOT DISTINCT a variant-less card could be inserted twice."""
    assert "UNIQUE NULLS NOT DISTINCT (set_id, card_number, variant)" in ddl(cards)


def test_an_external_identifier_is_unique_per_card_and_provider() -> None:
    assert "UNIQUE (card_id, provider, external_id)" in ddl(card_external_ids)


# ---------------------------------------------------------------------------
# A card cannot disagree with its own set
# ---------------------------------------------------------------------------
def test_game_and_language_are_held_to_the_set_by_a_composite_foreign_key() -> None:
    constraint = next(
        constraint
        for constraint in cards.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    )

    assert [column.name for column in constraint.columns] == ["set_id", "game", "language"]
    assert [element.target_fullname for element in constraint.elements] == [
        "sets.id",
        "sets.game",
        "sets.language",
    ]
    # Deleting a set out from under its cards is never what someone meant.
    assert constraint.ondelete == "RESTRICT"


def test_sets_carries_the_unique_constraint_that_foreign_key_requires() -> None:
    assert "UNIQUE (id, game, language)" in ddl(sets)


# ---------------------------------------------------------------------------
# The search contract (`CardRepository.search`)
# ---------------------------------------------------------------------------
def test_card_number_is_stored_verbatim_and_normalised_alongside() -> None:
    """'025/165' stays printed; '25' is what a user types and what is indexed."""
    assert cards.columns["card_number"].computed is None

    key = cards.columns["card_number_key"]
    assert key.computed is not None
    assert key.computed.persisted is True
    assert "ltrim(split_part(card_number, '/', 1), '0')" in ddl(cards)


@pytest.mark.parametrize(
    "index_name",
    [
        "ix_cards_game_language_card_number_key",
        "ix_cards_game_language_name",
        "ix_cards_set_id",
    ],
)
def test_the_search_filters_are_indexed(index_name: str) -> None:
    assert index_name in {index.name for index in cards.indexes}


# ---------------------------------------------------------------------------
# Collation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("table", "column"),
    [
        (sets, "set_code"),
        (sets, "name"),
        (cards, "card_number"),
        (cards, "card_number_key"),
        (cards, "name"),
        (cards, "variant"),
        (cards, "rarity"),
        (card_external_ids, "external_id"),
    ],
    ids=lambda value: value if isinstance(value, str) else value.name,
)
def test_printed_text_is_ordered_by_byte_not_by_server_locale(table: sa.Table, column: str) -> None:
    """The local database runs `--locale=C`; CI's inherits the image default.

    Declaring the collation per column is what makes the total order
    `CardRepository.search` promises mean the same thing in both.
    """
    assert table.columns[column].type.collation == "C"


# ---------------------------------------------------------------------------
# No extensions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("table", [sets, cards, card_external_ids], ids=SPEC_TABLES)
def test_the_schema_needs_no_postgresql_extension(table: sa.Table) -> None:
    """Spec §8 keeps the platform open between ordinary PostgreSQL and Supabase."""
    compiled = ddl(table).lower()
    assert "gin_trgm_ops" not in compiled
    assert "create extension" not in compiled


# ---------------------------------------------------------------------------
# ADR 0004 — no catalog card images in V1
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("column", ["image_front", "image_back"])
def test_image_columns_exist_but_are_optional(column: str) -> None:
    """§10 names them; ADR 0004 keeps them NULL, so nothing may require one."""
    assert cards.columns[column].nullable is True
    assert cards.columns[column].server_default is None
