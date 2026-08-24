"""Unit tests for the `grading_rules` table definition.

Every test here runs without PostgreSQL: it inspects the `MetaData` and, where
the point is the SQL, the DDL SQLAlchemy compiles for the PostgreSQL dialect.
`test_grading_schema.py` proves the same properties hold in a real database
after the migration has run; these prove they were declared on purpose.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_api.grading import tables
from tcg_api.grading.tables import grading_rules
from tcg_api.table_registry import DECLARED_TABLES

SECTION_23_COLUMNS = {"company", "version", "effective_from", "rules", "source"}


def ddl(table: sa.Table) -> str:
    return str(sa.schema.CreateTable(table).compile(dialect=postgresql.dialect()))


# ---------------------------------------------------------------------------
# Spec §23 — the columns
# ---------------------------------------------------------------------------
def test_the_grading_domain_declares_one_table_and_the_registry_saw_it() -> None:
    """A domain that is not in `table_registry` is invisible to Alembic.

    `env.py` reads the registry's `MetaData`, and autogenerate proposes dropping
    every table it cannot reach — so a module that declares a table and is never
    imported is worse than one that declares none.
    """
    assert {table.name for table in tables.TABLES} == {"grading_rules"}
    assert "grading_rules" in {table.name for table in DECLARED_TABLES}


def test_the_columns_are_section_23s_fields_plus_provenance() -> None:
    assert set(grading_rules.c.keys()) == SECTION_23_COLUMNS | {"verified_on", "created_at"}


def test_effective_to_is_not_a_column() -> None:
    """§23 names it; this schema derives it, and that departure is the design.

    A version is in force from its `effective_from` until the next version of the
    same company begins, so one company's intervals are `[fᵢ, fᵢ₊₁)` by
    construction and two of them cannot overlap. Storing the column instead would
    mean a superseding version has to UPDATE its predecessor — an exception
    carved into the immutability §23 asks for — and without `btree_gist`, which
    this schema does not install, there is no EXCLUDE constraint to stop two
    overlapping *closed* ranges. `tcg_api.grading.rules` computes it with
    `lead()`, so every record a caller receives still carries one.
    """
    assert "effective_to" not in grading_rules.c


def test_the_version_is_the_identity() -> None:
    """No surrogate key, because nothing references this table by one.

    `analyses.grading_rules_version` deliberately carries no foreign key, and
    `GradingRules` has no id field, so a uuid column would need a namespace
    constant and an id helper serving nothing.
    """
    assert [column.name for column in grading_rules.primary_key] == ["version"]


def test_only_the_two_dates_and_nothing_else_may_be_absent() -> None:
    """`effective_from` is nullable because TAG and BGS publish no effective date.

    Everything else is a fact the record cannot exist without: which company,
    which version, where it was read and when.
    """
    nullable = {column.name for column in grading_rules.c if column.nullable}
    assert nullable == {"effective_from"}


# ---------------------------------------------------------------------------
# The constraint that makes overlap impossible
# ---------------------------------------------------------------------------
def test_one_company_cannot_have_two_standards_starting_on_one_day() -> None:
    """This *is* the non-overlap enforcement, not a supplement to it.

    `NULLS NOT DISTINCT` is the load-bearing half: TAG and BGS state no effective
    date, and a default UNIQUE treats NULLs as distinct, so without it a company
    could carry two undated standards and "which was in force" would have no
    answer.
    """
    assert "UNIQUE NULLS NOT DISTINCT (company, effective_from)" in ddl(grading_rules)


def test_the_company_is_not_constrained_to_a_closed_vocabulary() -> None:
    """The one place `one_of` looks applicable and is not — spec §22.

    `GradingCompany` is a vocabulary rather than a closed enum precisely so that
    "a fourth company costs one new adapter and no caller change" stays true. A
    CHECK built from it would make a fourth company cost a migration of this
    table as well. `market_observations.grading_company` (#50) takes one for the
    opposite reason: a price row is data *about* a company V1 ships, where this
    is the company's own record.
    """
    checks = [
        constraint
        for constraint in grading_rules.constraints
        if isinstance(constraint, sa.CheckConstraint)
    ]
    assert checks == []


def test_printed_text_is_collated_c() -> None:
    """Equality and the resolver's PARTITION BY must mean one thing everywhere.

    The Compose database runs initdb with `--locale=C`; CI's PostgreSQL service
    inherits the image default.
    """
    assert grading_rules.c.version.type.collation == "C"
    assert grading_rules.c.company.type.collation == "C"


def test_the_table_needs_no_extension() -> None:
    """An EXCLUDE constraint would have needed `btree_gist`; the design avoids it.

    `test_catalog_schema.py` asserts the running database has installed nothing
    but `plpgsql`. This is the declaration-side half of that claim.
    """
    assert "create extension" not in ddl(grading_rules).lower()


# ---------------------------------------------------------------------------
# Immutability — spec §23, "do not overwrite old grading rules"
# ---------------------------------------------------------------------------
# These read the module's private DDL constants deliberately. Nothing public
# exposes them, Alembic compares no triggers, and a trigger that silently
# stopped guarding the table would fail no other test in this file.
def test_the_trigger_guards_update_and_delete() -> None:
    trigger = str(tables._IMMUTABLE_TRIGGER)
    assert "BEFORE UPDATE OR DELETE ON grading_rules" in trigger
    assert "FOR EACH ROW" in trigger


def test_the_trigger_has_no_when_clause() -> None:
    """Deliberately unlike `trg_analyses_reproducibility_immutable`.

    That one permits NULL → value because an analysis's reproducibility columns
    are filled in by later stages. A `grading_rules` row is written complete,
    from a constant, so there is no second write to permit — and an exception
    nobody needs is an exception somebody will widen.
    """
    assert "WHEN" not in str(tables._IMMUTABLE_TRIGGER)


def test_the_refusal_reports_a_constraint_violation() -> None:
    """SQLSTATE class 23, so SQLAlchemy raises `IntegrityError`.

    Any other errcode reaches a caller as `InternalError`, which every layer
    above would have to special-case.
    """
    assert "restrict_violation" in str(tables._IMMUTABLE_FUNCTION)


def test_each_ddl_statement_is_one_statement() -> None:
    """asyncpg prepares every statement it is handed, and prepares only one."""
    for statement in (tables._DROP_IMMUTABLE_TRIGGER, tables._DROP_IMMUTABLE_FUNCTION):
        assert str(statement).strip().rstrip(";").count(";") == 0


def test_the_trigger_body_survives_percent_interpolation() -> None:
    """`sa.DDL` runs its statement through Python's `%`.

    `RAISE EXCEPTION 'x %', arg` fails at compile time, which is why the body
    concatenates into `RAISE USING MESSAGE`. Compiling is the assertion.
    """
    for statement in (
        tables._IMMUTABLE_FUNCTION,
        tables._IMMUTABLE_TRIGGER,
        tables._DROP_IMMUTABLE_TRIGGER,
        tables._DROP_IMMUTABLE_FUNCTION,
    ):
        assert statement.compile(dialect=postgresql.dialect()) is not None
