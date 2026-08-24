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
from tcg_api.analysis.tables import analyses
from tcg_api.market import tables
from tcg_api.market.snapshots import _PRICES
from tcg_api.market.tables import (
    market_observations,
    market_providers,
    market_snapshots,
)
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

SECTION_36_SNAPSHOT_COLUMNS = {
    "id",
    "provider_id",
    "generated_at",
    "data_version",
}

VERSIONS = Path(__file__).resolve().parents[3] / "database" / "migrations" / "versions"

MIGRATION = (VERSIONS / "20260824_add_the_market_data_schema.py").read_text(encoding="utf-8")

SNAPSHOT_MIGRATION = (VERSIONS / "20260824_add_the_market_snapshot_schema.py").read_text(
    encoding="utf-8"
)


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
def test_the_market_domain_declares_three_tables_and_the_registry_saw_them() -> None:
    """A domain that is not in `table_registry` is invisible to Alembic.

    `env.py` reads the registry's `MetaData`, and autogenerate proposes dropping
    every table it cannot reach — so a module that declares a table and is never
    imported is worse than one that declares none.
    """
    assert {table.name for table in tables.TABLES} == {
        "market_providers",
        "market_observations",
        "market_snapshots",
    }
    declared = {table.name for table in DECLARED_TABLES}
    assert {"market_providers", "market_observations", "market_snapshots"} <= declared


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
# Spec §36 — a snapshot is a cut-line, not a copy
# ---------------------------------------------------------------------------
def test_the_snapshot_columns_are_section_36s_fields() -> None:
    """Three of §36's four are columns. The fourth is deliberately not one.

    §36 draws `observations` hanging off a snapshot, and a membership table is
    the obvious reading of that. It is refused twice over: the set is already
    determined by `generated_at` over an append-only table, and — the decisive
    one — a stored list could only hold the rows *this run wrote*, so a card the
    day's ingestion did not reach would read as having no price at all when a
    perfectly good one from yesterday is on file.
    """
    columns = set(market_snapshots.columns.keys())

    assert columns == SECTION_36_SNAPSHOT_COLUMNS
    assert "observations" not in columns
    assert "observation_count" not in columns


def test_the_data_version_is_generated_from_the_cut_line() -> None:
    """ADR 0006 fixes its content, so the database can fix its value.

    A run that could name its own `data_version` could name one disagreeing with
    when it was cut — the same drift `market_type` is generated to prevent.
    """
    computed = market_snapshots.c.data_version.computed

    assert computed is not None
    assert computed.persisted is True
    assert str(computed.sqltext) == tables._DATA_VERSION_EXPRESSION


def test_the_migration_generates_the_data_version_the_same_way() -> None:
    expression = re.search(r'DATA_VERSION_EXPRESSION = "(.*)"', SNAPSHOT_MIGRATION)

    assert expression is not None
    assert expression.group(1) == tables._DATA_VERSION_EXPRESSION


def test_the_cut_line_is_written_by_the_database() -> None:
    """No caller supplies `generated_at`, so no cut can be backdated.

    A `CHECK (generated_at <= now())` would say the same thing and is not
    available: `now()` is not IMMUTABLE, and a volatile expression in a CHECK
    breaks `pg_dump`/restore. Leaving the column to its default is free.
    """
    assert market_snapshots.c.generated_at.server_default is not None
    assert market_snapshots.c.generated_at.type.timezone is True


def test_a_snapshot_names_one_provider() -> None:
    """RESTRICT: a provider whose prices a snapshot resolves cannot be removed."""
    assert "REFERENCES market_providers (id) ON DELETE RESTRICT" in ddl(market_snapshots)


def test_two_snapshots_may_share_a_day() -> None:
    """`data_version` is a date, so a second run in one day repeats it.

    Deliberately no `UNIQUE (provider_id, data_version)`: two cuts on one day are
    two snapshots, resolved through different `generated_at` values and named by
    id, and refusing the second would fail for nothing anybody could act on.
    """
    assert not [
        constraint
        for constraint in market_snapshots.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    ]
    assert not market_snapshots.indexes


def test_an_analysis_points_at_a_snapshot_it_cannot_lose() -> None:
    """The key `analyses.market_snapshot_id`'s comment promised this milestone.

    RESTRICT, unlike `card_database_version` which carries no key at all: a
    catalog version is an identifier worth keeping even if the record went,
    where a snapshot *is* the prices.
    """
    key = next(
        constraint
        for constraint in analyses.foreign_key_constraints
        if constraint.name == "fk_analyses_market_snapshot_id_market_snapshots"
    )

    assert [column.name for column in key.columns] == ["market_snapshot_id"]
    assert key.ondelete == "RESTRICT"


# ---------------------------------------------------------------------------
# Resolving a snapshot
# ---------------------------------------------------------------------------
# The statement is compiled rather than executed. `test_market_schema.py` runs
# it against a real database; these two assert the properties that would fail
# silently — a resolution that returned the right prices in an unstable order,
# or one cut on the wrong column, passes every behavioural test that does not
# look for exactly this.
def test_the_resolution_orders_by_a_total_key() -> None:
    """`PriceObservation.history_key` names this module's problem by number.

    Two rows tying under a partial key could come back in either order, which
    would make an immutable snapshot resolve differently on two readings of the
    same data. `id` is the primary key, so ending on it makes the order total.
    """
    rendered = str(_PRICES.compile(dialect=postgresql.dialect()))
    ordering = rendered[rendered.index("ORDER BY") :]

    assert (
        "DISTINCT ON (market_observations.grading_company, market_observations.grade)" in rendered
    )
    assert ordering.rstrip().endswith("market_observations.id DESC")
    assert "market_observations.observed_at DESC" in ordering
    assert "market_observations.created_at DESC" in ordering


def test_raw_prices_sort_ahead_of_graded_ones() -> None:
    """`NULLS FIRST`, matching `history_key`'s empty company slug.

    Raw rows need no other special case: `grading_company IS NULL` compares equal
    to itself under `DISTINCT ON`, so they fall into exactly one group.
    """
    rendered = str(_PRICES.compile(dialect=postgresql.dialect()))

    assert "market_observations.grading_company ASC NULLS FIRST" in rendered


# ---------------------------------------------------------------------------
# Append-only
# ---------------------------------------------------------------------------
# These read the module's private DDL constants deliberately. Nothing public
# exposes them, Alembic compares no triggers, and a trigger that silently
# stopped guarding a table would fail no other test in this file.
def test_all_three_tables_are_guarded_against_update() -> None:
    assert "BEFORE UPDATE ON market_providers" in str(tables._PROVIDERS_TRIGGER)
    assert "BEFORE UPDATE ON market_observations" in str(tables._OBSERVATIONS_TRIGGER)
    assert "BEFORE UPDATE ON market_snapshots" in str(tables._SNAPSHOTS_TRIGGER)


def test_no_trigger_guards_delete() -> None:
    """Deliberate, and the one place this departs from `grading_rules`.

    A daily refresh over the whole catalog is millions of rows a year and will
    eventually need pruning. A provider row anything references is already
    undeletable, through the foreign key's RESTRICT.
    """
    for trigger in (
        tables._PROVIDERS_TRIGGER,
        tables._OBSERVATIONS_TRIGGER,
        tables._SNAPSHOTS_TRIGGER,
    ):
        assert "DELETE" not in str(trigger)


def test_one_function_serves_all_three_tables() -> None:
    """`TG_TABLE_NAME` names the table, so there is no second copy to keep in step."""
    function = str(tables._IMMUTABLE_FUNCTION)
    assert "TG_TABLE_NAME" in function
    for trigger in (
        tables._PROVIDERS_TRIGGER,
        tables._OBSERVATIONS_TRIGGER,
        tables._SNAPSHOTS_TRIGGER,
    ):
        assert str(trigger).count("market_rows_are_immutable()") == 1
    # And the snapshot migration creates none of its own: the market-data
    # revision it revises already did, and a second copy of a body means the
    # last revision to run silently wins if the two ever differ.
    assert "CREATE OR REPLACE FUNCTION" not in SNAPSHOT_MIGRATION


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
        tables._DROP_SNAPSHOTS_TRIGGER,
        tables._DROP_IMMUTABLE_FUNCTION,
    ):
        assert str(statement).strip().rstrip(";").count(";") == 0
