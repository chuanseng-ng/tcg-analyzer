"""Unit tests for the `economic_configurations` definition.

Every test here runs without PostgreSQL: it inspects the `MetaData` and, where
the point is the SQL, the DDL SQLAlchemy compiles for the PostgreSQL dialect.
`test_economics_schema.py` proves the same properties hold in a real database
after the migration has run; these prove they were declared on purpose.

Two properties are worth more than the rest, and both are inherited rather than
invented here:

* **There is no total.** #58 binds that §46's costs are named line items and
  that nothing computes a grand total, because §47's future dimensions attach to
  individual lines. A `total_costs` column would be that decision undone in the
  one place hardest to reverse.
* **The column names are the engine's field names.** `CostConfiguration` and
  `RecommendationThresholds` are what this table stores, so a renamed field
  fails here rather than quietly writing into a column that no longer matches.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_api.analysis.tables import analyses
from tcg_api.economics import tables
from tcg_api.economics.tables import economic_configurations
from tcg_api.table_registry import DECLARED_TABLES
from tcg_domain.money import Currency
from tcg_economic_engine import CostConfiguration, RecommendationThresholds

#: Spec §46's six, as the user configures them. `selling_fee` is one line item
#: with two parts, so it is two columns; the other five are one each.
SECTION_46_COST_COLUMNS = {
    "grading_fee",
    "outbound_shipping",
    "return_shipping",
    "insurance",
    "miscellaneous",
    "selling_fee_rate",
    "selling_fee_flat",
}

MIGRATION = Path("database/migrations/versions/20260827_add_the_economic_configuration_schema.py")


@pytest.fixture(scope="module")
def migration_source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def create_table() -> str:
    return str(sa.schema.CreateTable(economic_configurations).compile(dialect=postgresql.dialect()))


def test_the_table_is_declared_on_the_shared_metadata() -> None:
    assert economic_configurations in DECLARED_TABLES
    assert economic_configurations.metadata is analyses.metadata


def test_every_section_46_line_item_is_its_own_column() -> None:
    assert set(economic_configurations.c.keys()) >= SECTION_46_COST_COLUMNS


def test_nothing_stores_a_total() -> None:
    """#58: named line items, never a total. §47 attaches tax and tiers per line."""
    assert not [name for name in economic_configurations.c.keys() if "total" in name]  # noqa: SIM118


def test_the_cost_columns_are_the_engines_own_field_names() -> None:
    """A renamed `CostConfiguration` field fails here, not silently at runtime."""
    fields = {field.name for field in dataclasses.fields(CostConfiguration)}
    committed = SECTION_46_COST_COLUMNS - {"selling_fee_rate", "selling_fee_flat"}
    assert committed < fields
    assert "selling_fee" in fields


def test_the_threshold_columns_are_the_engines_own_field_names() -> None:
    """#64's five, stored so a recommendation stays reproducible under §57."""
    thresholds = {field.name for field in dataclasses.fields(RecommendationThresholds)}
    assert thresholds <= set(economic_configurations.c.keys())
    assert len(thresholds) == 5


def test_the_acquisition_cost_is_the_only_nullable_amount() -> None:
    """§45: absent is not zero. Every other amount is a value the user gave."""
    assert economic_configurations.c.acquisition_cost.nullable
    for name in SECTION_46_COST_COLUMNS:
        assert not economic_configurations.c[name].nullable


def test_the_optimization_mode_carries_no_check(create_table: str) -> None:
    """#63: a mode is a `str` and a sixth needs no migration. The boundary validates."""
    assert "optimization_mode" in economic_configurations.c
    assert "optimization_mode IN (" not in create_table


def test_the_company_selection_carries_no_membership_check(create_table: str) -> None:
    """`grading_rules.company` carries none either: a fourth company is one adapter."""
    assert "'psa'" not in create_table
    assert "ck_economic_configurations_at_least_one_grading_company" in create_table


def test_the_currency_admits_only_what_the_domain_models(create_table: str) -> None:
    """Unlike `market_observations.currency`, which records what a provider said."""
    for currency in Currency:
        assert f"'{currency.value}'" in create_table


def test_every_amount_is_refused_below_zero(create_table: str) -> None:
    for name in (*SECTION_46_COST_COLUMNS, "minimum_incremental_profit"):
        if name == "selling_fee_rate":
            continue
        assert f"{name} >= 0" in create_table


def test_the_selling_fee_rate_is_a_proportion(create_table: str) -> None:
    """#58: ten percent is 0.10, and `Decimal("10")` is refused rather than read as 1000%."""
    assert "selling_fee_rate >= 0 AND selling_fee_rate <= 1" in create_table


def test_every_confidence_threshold_is_a_unit_interval(create_table: str) -> None:
    for name in ("minimum_image_quality", "minimum_grade_confidence", "minimum_figure_confidence"):
        assert f"{name} >= 0 AND {name} <= 1" in create_table
    assert "maximum_unpriced_probability >= 0 AND maximum_unpriced_probability <= 1" in create_table


def test_the_row_is_immutable_against_update_and_not_against_delete() -> None:
    """`UPDATE` only, so spec §54's retention sweep can still delete one."""
    trigger = str(tables._IMMUTABLE_TRIGGER.statement)
    assert "BEFORE UPDATE ON economic_configurations" in trigger
    assert "DELETE" not in trigger


def test_the_analysis_now_points_at_a_real_table() -> None:
    """#58 deferred this key to #65 by name; the column had none until now."""
    (key,) = list(analyses.c.economic_configuration_id.foreign_keys)
    assert key.column is economic_configurations.c.id
    assert key.ondelete == "RESTRICT"


def test_the_migration_and_the_declaration_agree_on_every_check(migration_source: str) -> None:
    """Alembic compares a check's **name** and not its **text**, so this does.

    A migration is a snapshot of what was applied and is never regenerated, so a
    reworded condition here and not there is a database that refuses a value the
    application considers legal — with the drift guard silent, because the names
    still match.
    """
    prefix = "ck_economic_configurations_"
    checks = [
        constraint
        for constraint in economic_configurations.constraints
        if isinstance(constraint, sa.CheckConstraint)
    ]
    assert checks
    for constraint in checks:
        assert str(constraint.name).removeprefix(prefix) in migration_source
        assert str(constraint.sqltext) in migration_source


def test_every_constraint_name_fits_postgres_identifier_limit() -> None:
    """63 bytes, and the convention already spends 27 of them on the prefix.

    Over the limit, SQLAlchemy renders a truncated stem plus a hash and the
    reflected name no longer matches the declared one — which
    `alembic revision --autogenerate` then reports as a constraint dropped and
    re-added on every run, for ever. Found exactly that way.
    """
    for constraint in economic_configurations.constraints:
        if constraint.name:
            assert len(str(constraint.name).encode()) <= 63, constraint.name


def test_the_migration_creates_the_trigger(migration_source: str) -> None:
    assert "trg_economic_configurations_immutable" in migration_source
    assert "economic_configuration_is_immutable" in migration_source


def test_the_migration_adds_the_foreign_key(migration_source: str) -> None:
    assert "fk_analyses_economic_configuration_id_economic_configurations" in migration_source
    assert "RESTRICT" in migration_source
