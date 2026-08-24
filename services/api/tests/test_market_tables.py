"""Unit tests for the `market_providers` and `market_observations` definitions.

Every test here runs without PostgreSQL: it inspects the `MetaData` and, where
the point is the SQL, the DDL SQLAlchemy compiles for the PostgreSQL dialect.
`test_market_schema.py` proves the same properties hold in a real database after
the migration has run; these prove they were declared on purpose.

The migration writes the vocabularies and patterns out as literals, because a
migration is a snapshot of what was applied and must not change when the table
module does. Several tests below read that file and check the two still agree —
which is the only thing standing between a reworded enum and a database that
silently refuses a value the application considers legal.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_api.market import tables
from tcg_api.market.tables import market_observations, market_providers
from tcg_api.table_registry import DECLARED_TABLES
from tcg_domain.grade import Grade, GradeBound
from tcg_grading_companies import GradingCompany
from tcg_market_data import MarketType

SECTION_35_PROVIDER_COLUMNS = {
    "id",
    "name",
    "version",
    "license",
    "commercial_use",
    "terms_reference",
}

SECTION_35_OBSERVATION_COLUMNS = {
    "id",
    "card_id",
    "provider_id",
    "market_type",
    "grading_company",
    "grade",
    "currency",
    "price",
    "observed_at",
    "metadata",
}

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "database"
    / "migrations"
    / "versions"
    / "20260824_add_the_market_data_schema.py"
).read_text(encoding="utf-8")


def ddl(table: sa.Table) -> str:
    return str(sa.schema.CreateTable(table).compile(dialect=postgresql.dialect()))


def check_constraint(table: sa.Table, name: str) -> str:
    """The rendered text of one named CHECK, so a test can assert on the SQL."""
    marker = f"CONSTRAINT ck_{table.name}_{name} CHECK "
    rendered = ddl(table)
    assert marker in rendered, f"no constraint named {name} on {table.name}"
    return rendered.split(marker, 1)[1].split("\n", 1)[0]


# ---------------------------------------------------------------------------
# Spec §35 — the columns
# ---------------------------------------------------------------------------
def test_the_market_domain_declares_two_tables_and_the_registry_saw_them() -> None:
    """A domain that is not in `table_registry` is invisible to Alembic.

    `env.py` reads the registry's `MetaData`, and autogenerate proposes dropping
    every table it cannot reach — so a module that declares a table and is never
    imported is worse than one that declares none.
    """
    assert {table.name for table in tables.TABLES} == {
        "market_providers",
        "market_observations",
    }
    declared = {table.name for table in DECLARED_TABLES}
    assert {"market_providers", "market_observations"} <= declared


def test_the_provider_columns_are_section_35s_fields_plus_a_slug_and_provenance() -> None:
    """`slug` and `verified_on` are additions, and each has one job.

    §35 names `name`, and ADR 0006 binds the string 'PokePriceTracker' — the
    party the licence fields describe. What an observation carries is a
    lowercase slug, the way `card_external_ids.provider` already spells a
    source, so the two are separate columns rather than one derived from the
    other. `verified_on` is the field ADR 0006's ninety-day re-read applies to.
    """
    assert set(market_providers.c.keys()) == SECTION_35_PROVIDER_COLUMNS | {
        "slug",
        "verified_on",
        "created_at",
    }


def test_the_observation_columns_are_section_35s_fields_plus_confidence() -> None:
    """`confidence` is required by the port and has no home in §35's list.

    A column rather than a key in `metadata`, for the reason
    `images.quality_score` is one: a mandatory, range-validated field in an
    untyped bag quietly becomes optional.
    """
    assert set(market_observations.c.keys()) == SECTION_35_OBSERVATION_COLUMNS | {
        "confidence",
        "created_at",
    }


# ---------------------------------------------------------------------------
# `market_type` is generated, not written
# ---------------------------------------------------------------------------
def test_market_type_is_generated_from_the_grading_company() -> None:
    """`PriceObservation.market_type` derives it; so does the database.

    A row claiming 'raw' while carrying a grading company is therefore not
    representable at all, rather than refused by a constraint someone could
    later relax. `cards.card_number_key` is the precedent.
    """
    computed = market_observations.c.market_type.computed
    assert computed is not None
    assert computed.persisted is True
    assert "GENERATED ALWAYS AS" in ddl(market_observations)


def test_the_generated_expression_names_both_market_types() -> None:
    """Built from `MarketType`, not retyped, so a third member cannot go missing."""
    rendered = str(market_observations.c.market_type.computed.sqltext)
    assert {MarketType.RAW.value, MarketType.GRADED.value} == {"raw", "graded"}
    for value in MarketType:
        assert f"'{value.value}'" in rendered


def test_a_graded_row_still_needs_a_grade() -> None:
    """The generator reads only `grading_company`, so this is the other half.

    Without it a row could claim 'graded' and carry no grade, which is not a
    price for anything.
    """
    assert (
        check_constraint(market_observations, "graded_rows_carry_a_company_and_a_grade")
        == "((grading_company IS NULL) = (grade IS NULL)), "
    )


# ---------------------------------------------------------------------------
# Vocabularies and grammars
# ---------------------------------------------------------------------------
def test_the_grading_company_is_constrained_to_the_companies_v1_ships() -> None:
    """The opposite call from `grading_rules.company`, and deliberately so.

    A price row is data *about* a company V1 ships; a rules row is the company's
    own record, where a CHECK would make a fourth company cost a migration.
    """
    rendered = check_constraint(market_observations, "grading_company_is_a_supported_company")
    for company in GradingCompany:
        assert f"'{company.value}'" in rendered


def test_the_migration_lists_the_same_companies() -> None:
    """Alembic compares a CHECK's name but not its text; nothing else would notice."""
    listed = re.search(r'GRADING_COMPANIES = "(.*)"', MIGRATION)
    assert listed is not None
    assert listed.group(1) == ", ".join(f"'{company.value}'" for company in GradingCompany)


def test_the_migration_generates_market_type_the_same_way() -> None:
    expression = re.search(r'MARKET_TYPE_EXPRESSION = "(.*)"', MIGRATION)
    assert expression is not None
    assert expression.group(1) == str(market_observations.c.market_type.computed.sqltext)


@pytest.mark.parametrize(
    "key",
    ["0", "1", "1.5", "8.5", "9", "9.5", "10", "7_or_lower", "9.5_or_higher"],
)
def test_the_grade_pattern_accepts_every_key_the_domain_produces(key: str) -> None:
    """The CHECK is `tcg_domain.Grade`'s grammar, written as one expression.

    Half steps within [0, 10], plus §24's collapsed tails. Parsing the key first
    is what makes this a claim about the two agreeing rather than about the
    regular expression alone.
    """
    assert str(Grade.parse(key)) == key
    assert re.match(tables._GRADE_KEY_PATTERN, key)


@pytest.mark.parametrize("key", ["10.5", "11", "9.25", "9.0", "", "psa 10", "9_or_middling"])
def test_the_grade_pattern_refuses_what_the_domain_refuses(key: str) -> None:
    """`9.0` is a real grade spelled wrong; `Grade` canonicalises it to `9`.

    Storing both spellings would give one grade two database keys, so the
    non-canonical form is refused here rather than normalised.
    """
    assert not re.match(tables._GRADE_KEY_PATTERN, key)


def test_the_grade_pattern_knows_no_company_scale() -> None:
    """PSA issues no 9.5 and BGS does — and this column does not know that.

    A per-company CHECK would make a fourth company, or a scale revision, cost a
    migration of this table. `tcg_market_data.validated_grade_key` is the
    per-company guard, and neither substitutes for the other.
    """
    assert re.match(tables._GRADE_KEY_PATTERN, str(Grade(Grade.parse("9.5").value)))
    rendered = check_constraint(market_observations, "grade_is_a_grade_key")
    for company in GradingCompany:
        assert company.value not in rendered


def test_a_bucket_key_is_storable() -> None:
    """§24 collapses a distribution's tail, and the issue calls those legal values."""
    bucket = Grade(Grade.parse("7").value, GradeBound.OR_LOWER)
    assert re.match(tables._GRADE_KEY_PATTERN, str(bucket))


@pytest.mark.parametrize("code", ["SGD", "USD", "JPY"])
def test_the_currency_pattern_admits_any_iso_4217_code(code: str) -> None:
    """V1 reports SGD and converts nothing, but the provider prices in USD.

    An observation records what the provider said; a column admitting one value
    would be SGD hard-coded rather than §35's currency column.
    """
    assert re.match(tables._CURRENCY_PATTERN, code)


@pytest.mark.parametrize("code", ["sgd", "SG", "SGDD", "S G"])
def test_the_currency_pattern_refuses_anything_else(code: str) -> None:
    assert not re.match(tables._CURRENCY_PATTERN, code)


def test_the_provider_slug_pattern_is_the_domains_own() -> None:
    """Mirrors `tcg_domain.card.validated_slug`, so the two refuse the same strings."""
    from tcg_domain.card import validated_slug

    for slug in ("pokepricetracker", "manual", "pkmn-prices"):
        assert validated_slug("provider", slug, error=ValueError) == slug
        assert re.match(tables._SLUG_PATTERN, slug)
    for bad in ("PokePriceTracker", "poke_prices", "-manual", ""):
        assert not re.match(tables._SLUG_PATTERN, bad)


# ---------------------------------------------------------------------------
# Money, and the things that must not drift
# ---------------------------------------------------------------------------
def test_the_price_is_an_exact_decimal_quantised_to_the_cent() -> None:
    """Never floating point, and two places to match `Money`'s own quantisation."""
    price = market_observations.c.price.type
    assert isinstance(price, sa.Numeric)
    assert not isinstance(price, sa.Float)
    assert (price.precision, price.scale) == (12, 2)


def test_a_zero_price_is_allowed_and_a_negative_one_is_not() -> None:
    """Zero is a real observation about a card nobody will pay for.

    It is the one value that must never be confused with an absent price, which
    is why the port returns `InsufficientInformation` for the latter.
    """
    assert check_constraint(market_observations, "price_is_not_negative").startswith("(price >= 0)")


def test_confidence_is_a_unit_interval() -> None:
    assert (
        check_constraint(market_observations, "confidence_is_a_unit_interval")
        == "(confidence >= 0 AND confidence <= 1), "
    )


def test_printed_text_is_collated_c() -> None:
    """Ordering has to mean the same thing on the C-locale Compose database and CI's."""
    assert market_observations.c.grade.type.collation == "C"
    assert market_observations.c.grading_company.type.collation == "C"
    assert market_providers.c.slug.type.collation == "C"
    # Compared for equality only, so it takes the server's collation.
    assert market_observations.c.currency.type.collation is None


# ---------------------------------------------------------------------------
# Keys and indexes
# ---------------------------------------------------------------------------
def test_a_card_keeps_its_price_history_when_the_catalog_changes() -> None:
    """RESTRICT, as `analyses.card_id` is — never CASCADE and never SET NULL."""
    rendered = ddl(market_observations)
    assert "REFERENCES cards (id) ON DELETE RESTRICT" in rendered
    assert "REFERENCES market_providers (id) ON DELETE RESTRICT" in rendered


def test_one_provider_carries_one_row_per_published_version() -> None:
    """`NULLS NOT DISTINCT` is the load-bearing half: no provider publishes one.

    Without it a provider could carry two undated rows and "under which terms
    was this gathered" would have no answer.
    """
    assert "UNIQUE NULLS NOT DISTINCT (slug, version)" in ddl(market_providers)


def test_two_indexes_serve_all_three_lookups() -> None:
    """Leading column answers "by card"; the prefix answers (card, company, grade).

    "The latest price for this card at this grade" is a backward scan of the
    tail, so there is no DESC variant, and no third index.
    """
    indexes = {
        index.name: [column.name for column in index.columns]
        for index in market_observations.indexes
    }
    assert indexes == {
        "ix_market_observations_card_company_grade_observed_at": [
            "card_id",
            "grading_company",
            "grade",
            "observed_at",
        ],
        "ix_market_observations_observed_at": ["observed_at"],
    }


def test_every_identifier_fits_postgresqls_limit() -> None:
    """63 bytes, and the convention's own rendering of the wide index is exactly 63.

    A name over the limit is truncated silently, and a truncated name is one a
    later migration cannot drop by the name it wrote.
    """
    for table in tables.TABLES:
        names = (
            [table.name]
            + [constraint.name for constraint in table.constraints if constraint.name]
            + [index.name for index in table.indexes if index.name]
        )
        for name in names:
            assert len(str(name).encode("utf-8")) <= 63, name


def test_the_tables_need_no_extension() -> None:
    """Spec §8 keeps the platform open between ordinary PostgreSQL and Supabase."""
    for table in tables.TABLES:
        assert "create extension" not in ddl(table).lower()


# ---------------------------------------------------------------------------
# Append-only
# ---------------------------------------------------------------------------
# These read the module's private DDL constants deliberately. Nothing public
# exposes them, Alembic compares no triggers, and a trigger that silently
# stopped guarding a table would fail no other test in this file.
def test_both_tables_are_guarded_against_update() -> None:
    assert "BEFORE UPDATE ON market_providers" in str(tables._PROVIDERS_TRIGGER)
    assert "BEFORE UPDATE ON market_observations" in str(tables._OBSERVATIONS_TRIGGER)


def test_neither_trigger_guards_delete() -> None:
    """Deliberate, and the one place this departs from `grading_rules`.

    A daily refresh over the whole catalog is millions of rows a year and will
    eventually need pruning. A provider row anything references is already
    undeletable, through the foreign key's RESTRICT.
    """
    for trigger in (tables._PROVIDERS_TRIGGER, tables._OBSERVATIONS_TRIGGER):
        assert "DELETE" not in str(trigger)


def test_one_function_serves_both_tables() -> None:
    """`TG_TABLE_NAME` names the table, so there is no second copy to keep in step."""
    function = str(tables._IMMUTABLE_FUNCTION)
    assert "TG_TABLE_NAME" in function
    assert str(tables._PROVIDERS_TRIGGER).count("market_rows_are_immutable()") == 1
    assert str(tables._OBSERVATIONS_TRIGGER).count("market_rows_are_immutable()") == 1


def test_the_refusal_reports_a_constraint_violation() -> None:
    """SQLSTATE class 23, so SQLAlchemy raises `IntegrityError`."""
    assert "restrict_violation" in str(tables._IMMUTABLE_FUNCTION)


def test_the_migration_declares_the_same_trigger_body() -> None:
    """Two copies by design; this is what keeps them one behaviour."""
    for fragment in ("TG_TABLE_NAME", "restrict_violation", "market_rows_are_immutable()"):
        assert fragment in MIGRATION


def test_each_ddl_statement_is_one_statement() -> None:
    """asyncpg prepares every statement it is handed, and prepares only one."""
    for statement in (
        tables._DROP_PROVIDERS_TRIGGER,
        tables._DROP_OBSERVATIONS_TRIGGER,
        tables._DROP_IMMUTABLE_FUNCTION,
    ):
        assert str(statement).strip().rstrip(";").count(";") == 0
