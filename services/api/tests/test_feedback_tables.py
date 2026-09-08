"""Unit tests for spec §68's feedback table — #270.

Every test here runs without PostgreSQL: it inspects the `MetaData` and the
migration's source. `test_feedback_schema.py` proves the same properties hold in
a real database after the migration has run; these prove they were declared on
purpose.

Three properties are worth more than the rest:

* **The row carries nothing a session carried.** No `session_id`, no
  `analysis_id`, no image, no address — that is what `docs/retention.md`'s
  exemption promises in exchange for a hundred and eighty days, and a column
  added later would break the promise silently. The test is a whole-column-set
  assertion rather than a spot check for exactly that reason.
* **The status walks a branch, not a line.** `model_bundles` compares
  `array_position`, which says only "later than" and would let `validated`
  become `rejected`. The rendered trigger is asserted to contain the three
  legal pairs and to contain no `array_position` at all.
* **The code cannot be stored.** The column takes 64 lowercase hex and the
  renderer emits upper case from a 32-symbol alphabet, so the two are disjoint
  and the column could not hold a code even by accident.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from tcg_api.analysis.tables import REPRODUCIBILITY_COLUMNS, analyses
from tcg_api.feedback.tables import (
    TABLES,
    TRANSITIONS,
    FeedbackStatus,
    grade_feedback,
)
from tcg_api.table_registry import DECLARED_TABLES
from tcg_api.tables import ISSUED_GRADE_PATTERN
from tcg_economic_engine import RecommendedAction
from tcg_grading_companies import Designation, GradingCompany

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    REPO_ROOT
    / "database"
    / "migrations"
    / "versions"
    / "20260908_keep_a_prediction_for_a_reported_grade.py"
)

#: Every column, by name. Asserted whole rather than by membership: the point of
#: this table is what it does *not* hold, and only an equality catches an
#: addition.
EXPECTED_COLUMNS = {
    "id",
    "return_code_hash",
    "card_id",
    "predictions",
    "model_bundle_version",
    "grading_rules_version",
    "recommended_action",
    "status",
    "created_at",
    "expires_at",
    "grading_company",
    "grade",
    "designation",
    "certification_number",
    "submitted_at",
}

#: Words that would mean the row had been joined back to a session. Spec §53 and
#: §54, and the exemption `docs/retention.md` carries.
FORBIDDEN_FRAGMENTS = ("session", "analysis", "image", "address", "ip_", "user_agent")


@pytest.fixture(scope="module")
def migration_source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    """The migration, imported, so its literals can be compared by value.

    A migration is a snapshot of what was applied and is deliberately not
    importable from `tables.py`; loading it here is how the two copies are held
    to each other without either reading the other.
    """
    spec = importlib.util.spec_from_file_location("feedback_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_domain_is_registered() -> None:
    """A domain the registry does not import is one autogenerate proposes dropping."""
    assert set(TABLES) <= set(DECLARED_TABLES)


def test_the_one_table_is_declared_on_the_shared_metadata() -> None:
    assert {table.name for table in TABLES} == {"grade_feedback"}


def test_the_row_holds_nothing_that_names_a_session() -> None:
    """The whole of what `docs/retention.md` promised in exchange for 180 days.

    A `session_id` or an `analysis_id` here would make a prediction that
    outlives every photograph joinable to the browser token that produced it,
    which is exactly what spec §53's "do not permanently tie analyses to
    personal identity" argues against. `card_id` names a *printed card*, not a
    person.
    """
    assert {column.name for column in grade_feedback.columns} == EXPECTED_COLUMNS

    for column in grade_feedback.columns:
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in column.name, f"{column.name} names {fragment}"


def test_every_column_but_the_obvious_ones_explains_itself() -> None:
    """A column whose NULL means something says what it means."""
    undocumented = {
        column.name
        for column in grade_feedback.columns
        if column.comment is None and column.name not in {"id", "created_at"}
    }

    assert undocumented == set()


def test_the_return_code_column_cannot_hold_a_rendered_code() -> None:
    """64 lowercase hex, and the renderer emits upper case. Disjoint by construction."""
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in grade_feedback.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }

    assert (
        checks["ck_grade_feedback_return_code_is_stored_as_a_digest"]
        == "return_code_hash ~ '^[0-9a-f]{64}$'"
    )


def test_the_grade_check_is_the_shared_grammar() -> None:
    """The scale is Python's (#165). Hoisted, so the two writers cannot drift."""
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in grade_feedback.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }

    assert (
        checks["ck_grade_feedback_grade_is_an_issued_grade"]
        == f"grade IS NULL OR grade ~ '{ISSUED_GRADE_PATTERN}'"
    )
    # The rule the pattern exists to state: a slab prints one point, so §24's
    # collapsed tails are not grades a company issued.
    assert "_or_lower" not in ISSUED_GRADE_PATTERN
    assert "_or_higher" not in ISSUED_GRADE_PATTERN


def test_the_answer_is_recorded_exactly_when_it_is_answered() -> None:
    """#265's strict-both-ways idiom: no half-answered row is representable."""
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in grade_feedback.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    answered = checks["ck_grade_feedback_answer_is_recorded_exactly_when_answered"]

    assert "status = 'awaiting'" in answered
    assert "num_nonnulls(" in answered
    # A designation with no grade stays legal: PSA issues `authentic` in place
    # of a grade (#165), and requiring one would refuse an honest report.
    assert "grade IS NOT NULL OR designation IS NOT NULL" in answered


def test_the_status_trigger_is_a_branch_and_not_a_ladder(migration: ModuleType) -> None:
    """`array_position` says only "later than", and the two verdicts are level.

    This is the one rule `model_bundles`' trigger could not be copied for. A
    linear comparison legalises `validated` to `rejected`, which would let a
    reviewer change a verdict in place — and spec §68 makes validation a
    deliberate act. Both copies of the statement are checked, because the
    migration writes its own and nothing else holds the two together.
    """
    from tcg_api.feedback import tables

    for rendered in (str(tables._STATUS_FUNCTION), migration._STATUS_FUNCTION):
        for before, after in TRANSITIONS:
            assert f"(OLD.status = '{before}' AND NEW.status = '{after}')" in rendered
        # Nothing beyond the three, so a fourth cannot be smuggled in beside them.
        assert rendered.count("OLD.status = ") == len(TRANSITIONS)
        assert "array_position" not in rendered

    illegal = (
        (FeedbackStatus.AWAITING, FeedbackStatus.VALIDATED),
        (FeedbackStatus.AWAITING, FeedbackStatus.REJECTED),
        (FeedbackStatus.VALIDATED, FeedbackStatus.REJECTED),
        (FeedbackStatus.REJECTED, FeedbackStatus.VALIDATED),
        (FeedbackStatus.SUBMITTED, FeedbackStatus.AWAITING),
    )
    for before, after in illegal:
        assert (before, after) not in TRANSITIONS


def test_the_two_triggers_model_bundles_has_are_absent(migration_source: str) -> None:
    """Both absences are load-bearing, so both are asserted.

    An immutability trigger would be a second copy of a rule the one writer
    already keeps. An undeletable trigger would make the hourly sweep
    impossible and turn `docs/retention.md` into a document the schema
    contradicts.
    """
    assert "grade_feedback_are_immutable" not in migration_source
    assert "BEFORE DELETE ON grade_feedback" not in migration_source


def test_the_migration_writes_the_vocabularies_as_literals(migration: ModuleType) -> None:
    """A migration is a snapshot; the two copies are held to each other here."""

    def rendered(values: object) -> str:
        return ", ".join(f"'{value}'" for value in values)  # type: ignore[union-attr]

    assert rendered(FeedbackStatus) == migration.FEEDBACK_STATUSES
    assert rendered(GradingCompany) == migration.GRADING_COMPANIES
    assert rendered(Designation) == migration.DESIGNATIONS
    assert rendered(RecommendedAction) == migration.RECOMMENDED_ACTIONS
    assert migration.ISSUED_GRADE_PATTERN == ISSUED_GRADE_PATTERN


def test_the_card_reference_restricts(migration_source: str) -> None:
    """Nothing deletes from the catalog (#27), and a null card is a prediction
    about nothing."""
    key = next(iter(grade_feedback.c.card_id.foreign_keys))

    assert key.column.table.name == "cards"
    assert key.ondelete == "RESTRICT"
    assert grade_feedback.c.card_id.nullable is False


def test_the_sweep_has_its_index() -> None:
    """`ix_analysis_sessions_expires_at`'s counterpart, and the only index here."""
    indexes = {
        index.name: [column.name for column in index.columns] for index in grade_feedback.indexes
    }

    assert indexes == {"ix_grade_feedback_expires_at": ["expires_at"]}


def test_the_mint_marker_is_not_a_reproducibility_column() -> None:
    """`feedback_minted_at` is written after the §57 record is sealed.

    The immutability trigger's WHEN clause names `REPRODUCIBILITY_COLUMNS` and
    only those, so the mint's UPDATE does not fire it. If this ever fails, the
    mint has started raising `restrict_violation` on a healthy analysis.
    """
    assert "feedback_minted_at" in analyses.c
    assert analyses.c.feedback_minted_at.nullable is True
    assert "feedback_minted_at" not in REPRODUCIBILITY_COLUMNS
