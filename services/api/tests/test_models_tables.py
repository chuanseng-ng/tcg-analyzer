"""Unit tests for the model registry table — #189.

Every test here runs without PostgreSQL: it inspects the `MetaData` and the
migration's source. `test_models_schema.py` proves the same properties hold in
a real database after the migration has run; these prove they were declared on
purpose.

Three properties are worth more than the rest:

* **Only `status` may change, and only forward.** A registered bundle is what
  an analysis's `model_bundle_version` will mean forever, so every other
  column is write-once and a DELETE is refused outright — where the datasets
  domain leaves DELETE open for §54's disposal, a registry row is the record
  that a version existed and must outlive the artifact's retirement.
* **One `production` bundle per model name.** Spec §59's "never overwrite
  production model files", held by a partial unique index rather than by an
  operator remembering to retire the predecessor first.
* **`training_dataset_version` references the identifier, not the surrogate.**
  The row stays legible without a join — identifiers and hashes are how every
  other consumer names a dataset version — and RESTRICT still holds: a corpus
  a registered model trained on cannot be un-published.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from tcg_api.models.tables import (
    LIFECYCLE,
    RECORD_COLUMNS,
    TABLES,
    ModelStatus,
    model_bundles,
)
from tcg_api.table_registry import DECLARED_TABLES
from tcg_api.tables import one_of
from tcg_domain import VERSION_PATTERN

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    REPO_ROOT / "database" / "migrations" / "versions" / "20260831_add_the_model_registry.py"
)

#: Spec §58's `Store:` list, verbatim. `created_at` and `status` included —
#: the section names all eight, and a ninth column would be a scope question.
SECTION_58_FIELDS = (
    "model_name",
    "model_version",
    "training_dataset_version",
    "training_config",
    "metrics",
    "artifact_location",
    "created_at",
    "status",
)

#: The two CHECKs the migration builds from a named constant rather than
#: writing out. Excluded from the generic source search below and asserted by
#: value instead, which is stronger — `test_datasets_tables.py`'s pattern.
COMPOSED_CHECKS = (
    "ck_model_bundles_name_is_a_lowercase_slug",
    "ck_model_bundles_version_is_an_explicit_identifier",
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    """The migration, imported, so its constants can be compared by value.

    A migration is a snapshot of what was applied and is deliberately not
    importable from `tables.py`; loading it here is how the two copies are held
    to each other without either reading the other.
    """
    spec = importlib.util.spec_from_file_location("models_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_domain_is_registered() -> None:
    """A domain the registry does not import is one autogenerate proposes dropping."""
    assert set(TABLES) <= set(DECLARED_TABLES)


def test_the_one_table_is_declared_on_the_shared_metadata() -> None:
    assert {table.name for table in TABLES} == {"model_bundles"}


def test_section_58s_eight_fields_are_all_columns() -> None:
    """Spec §58's `Store:` list, every entry a column and by its own name."""
    columns = {column.name for column in model_bundles.columns}

    assert set(SECTION_58_FIELDS) <= columns
    assert columns - set(SECTION_58_FIELDS) == {"id"}


def test_every_column_is_not_null() -> None:
    """A bundle with an unstated field is not a smaller registration.

    The datasets domain's nullable rights columns exist so one gate refuses
    them under one name; nothing here is gated, so nothing here is nullable.
    """
    for column in model_bundles.columns:
        assert not column.nullable, column.name


def test_only_created_at_carries_a_server_default() -> None:
    """`status` in particular: the registration path writes `experimental`
    explicitly, because a default is a state nobody chose."""
    for column in model_bundles.columns:
        if column.name == "created_at":
            assert column.server_default is not None
        else:
            assert column.server_default is None, column.name


def test_the_training_dataset_version_references_the_identifier() -> None:
    """The identifier, not the surrogate key — and RESTRICT, for #153's reason:
    a version a registered model trained on cannot be un-published."""
    keys = list(model_bundles.c.training_dataset_version.foreign_keys)

    assert len(keys) == 1
    assert keys[0].column.table.name == "dataset_versions"
    assert keys[0].column.name == "version"
    assert keys[0].ondelete == "RESTRICT"


def test_the_model_version_is_unique() -> None:
    unique = [
        constraint
        for constraint in model_bundles.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    ]

    assert [[column.name for column in constraint.columns] for constraint in unique] == [
        ["model_version"]
    ]


def test_the_lifecycle_is_spec_58s_four_states_in_order() -> None:
    """The order is load-bearing: the transition trigger compares positions."""
    assert LIFECYCLE == ("experimental", "candidate", "production", "retired")
    assert tuple(status.value for status in ModelStatus) == LIFECYCLE


def test_the_status_vocabulary_is_a_check() -> None:
    """A closed lifecycle gets a CHECK like `DatasetSplit`'s — this is not
    `grading_rules.company`, where a new member should cost no migration."""
    assert one_check("status_is_a_known_status") == one_of("status", ModelStatus)


def test_the_version_grammar_is_the_dataset_versions_grammar(migration: ModuleType) -> None:
    """One grammar for every immutable identifier, so '/latest/' is unstorable
    here exactly as it is on `dataset_versions.version` (§59)."""
    assert VERSION_PATTERN.pattern == migration.VERSION_PATTERN
    assert one_check("version_is_an_explicit_identifier") == (
        f"model_version ~ '{migration.VERSION_PATTERN}'"
    )


def test_the_name_grammar_is_shared_with_the_migration(migration: ModuleType) -> None:
    assert one_check("name_is_a_lowercase_slug") == f"model_name ~ '{migration.NAME_PATTERN}'"


def test_the_version_must_name_its_model_exactly() -> None:
    """`grading-psa-v0.2.0` belongs to `grading-psa`, and to nothing shorter —
    a starts-with check would misfile 'grading-vault-v1.0.0' under 'grading',
    and the one-production index would then scope the wrong family.
    `model_name` is regex-safe under its own slug CHECK."""
    assert one_check("version_names_its_model") == (
        r"model_version ~ ('^' || model_name || '-v[0-9]+\.[0-9]+\.[0-9]+$')"
    )


def test_a_latest_location_is_unstorable() -> None:
    """Spec §59 verbatim — a moving pointer is not a location."""
    assert one_check("location_never_says_latest") == (
        "position('latest' in artifact_location) = 0"
    )


def test_a_blank_location_is_unstorable() -> None:
    assert one_check("location_is_not_blank") == "btrim(artifact_location) <> ''"


def test_one_production_per_name_is_a_partial_unique_index() -> None:
    """Spec §59's "never overwrite production model files" in schema form."""
    index = one_index("uq_model_bundles_one_production_per_name")

    assert index.unique
    assert [column.name for column in index.columns] == ["model_name"]
    assert str(index.dialect_options["postgresql"]["where"]) == "status = 'production'"


def test_the_write_once_columns_are_everything_but_status(migration: ModuleType) -> None:
    """The trigger's WHEN clause is rendered from this tuple, so a column added
    to the table is guarded by adding it here — or the test fails."""
    assert set(RECORD_COLUMNS) == {column.name for column in model_bundles.columns} - {"status"}
    assert migration.RECORD_COLUMNS == RECORD_COLUMNS


# ---------------------------------------------------------------------------
# The migration, and the drift the drift guard cannot see
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES, ids=lambda table: str(table.name))
def test_the_migration_and_the_declaration_agree_on_every_check(
    table: sa.Table, migration_source: str
) -> None:
    """Alembic compares a check's *name* but never its *text*."""
    prefix = f"ck_{table.name}_"
    checks = [
        constraint for constraint in table.constraints if isinstance(constraint, sa.CheckConstraint)
    ]

    assert checks, f"{table.name} declares no CHECK"
    for constraint in checks:
        assert str(constraint.name).removeprefix(prefix) in migration_source
        if str(constraint.name) in COMPOSED_CHECKS:
            continue
        assert str(constraint.sqltext) in migration_source


def test_the_migration_creates_two_functions_and_three_triggers(migration_source: str) -> None:
    """Two functions because the two refusals give different instructions: one
    says register a new version, the other names the illegal move."""
    assert migration_source.count("CREATE OR REPLACE FUNCTION") == 2
    assert "model_bundles_are_immutable" in migration_source
    assert "model_bundle_status_walks_forward" in migration_source
    assert "trg_model_bundles_immutable" in migration_source
    assert "trg_model_bundles_undeletable" in migration_source
    assert "trg_model_bundles_status_walks_forward" in migration_source


def test_the_record_trigger_fires_only_when_a_write_once_column_moves(
    migration_source: str, migration: ModuleType
) -> None:
    """`status`, the one mutable column, must not wake the record trigger —
    the reproducibility trigger's WHEN-clause precedent. Asserted on the
    rendered WHEN clause, because the source file only holds the f-string
    placeholder."""
    assert "BEFORE UPDATE ON model_bundles" in migration_source
    assert "NEW.status" not in migration.RECORD_CHANGED


def test_the_status_trigger_fires_only_on_a_status_change(migration_source: str) -> None:
    assert "WHEN (OLD.status IS DISTINCT FROM NEW.status)" in migration_source


def test_delete_is_refused_by_its_own_trigger(migration_source: str) -> None:
    """Unlike every other domain here, DELETE is refused: `retired` is the
    terminal state, and a registry row is the record that a version existed."""
    assert "BEFORE DELETE ON model_bundles" in migration_source
    assert "BEFORE UPDATE OR DELETE" not in migration_source


def test_the_migration_inserts_no_rows(migration_source: str) -> None:
    """Heuristic analyzer versions are code constants, never registry rows —
    the first row is written when the first trained artifact exists."""
    assert "op.bulk_insert" not in migration_source
    assert "INSERT INTO" not in migration_source


def test_the_downgrade_drops_both_functions_by_name(migration_source: str) -> None:
    """`DROP TABLE` takes each trigger with it, but never the functions."""
    assert "DROP FUNCTION IF EXISTS model_bundles_are_immutable()" in migration_source
    assert "DROP FUNCTION IF EXISTS model_bundle_status_walks_forward()" in migration_source


def test_every_constraint_name_fits_postgres_identifier_limit() -> None:
    """63 bytes — an over-long name reflects back truncated, and autogenerate
    then reports a drop-and-re-add on every run, for ever."""
    for table in TABLES:
        for constraint in (*table.constraints, *table.indexes):
            if constraint.name:
                assert len(str(constraint.name).encode()) <= 63, constraint.name


def one_check(short_name: str) -> str:
    """The text of one named CHECK, by the short name the convention prefixes."""
    full = f"ck_model_bundles_{short_name}"
    for constraint in model_bundles.constraints:
        if isinstance(constraint, sa.CheckConstraint) and constraint.name == full:
            return str(constraint.sqltext)
    raise AssertionError(f"{full} is not declared on model_bundles")


def one_index(name: str) -> sa.Index:
    for index in model_bundles.indexes:
        if index.name == name:
            return index
    raise AssertionError(f"{name} is not declared on model_bundles")
