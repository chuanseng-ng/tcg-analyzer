"""add the reproducibility record to analyses

Spec §57 requires every analysis to record eight things so that a historical
answer can be re-derived rather than re-guessed. Five of them already had
somewhere to live: `analysis_id` is the primary key, the input image hashes are
`images.sha256`, and `model_bundle_version`, `market_snapshot_id` and
`economic_configuration_id` are columns the §12 transcription created and
nothing has yet filled.

This revision adds the remaining three.

`card_database_version` and `grading_rules_version` are §57's, and §12's column
list has neither — the analysis revision transcribed §12 and stopped, leaving
these to the issue that owns the record end to end. `card_database_version`
holds a published `card_database_versions.version`, resolved when the analysis
ran; there is no foreign key, because the identifier is the fact worth keeping
and an analysis must not be blocked by a version record's lifetime.

`application_version` is the one that looks like a duplicate and is not. §12
puts it on `analysis_sessions`, and a session lives for days: a deployment
inside one makes the version that *opened* the session a different fact from the
version that *ran* the analysis. §57 asks for the second.

All three are nullable with no backfill. NULL means "no run has claimed this
analysis yet", which is true of every existing row; `grading_rules_version` is
additionally NULL for the life of V1, because no grading rules exist. A
documented absence is not the same as a missing field, which is precisely why
the column is created now rather than when something can fill it.

**Immutability is a trigger, not a convention.** A reproducibility record that
can be edited records nothing. `trg_analyses_reproducibility_immutable` fires
`BEFORE UPDATE` and refuses to change any of the six §57 columns that already
holds a value, raising `restrict_violation` so it reaches a caller as an
`IntegrityError` — the same shape as every other constraint in this schema.

Three things about that trigger are deliberate:

* **`UPDATE` only.** The card-database-version trigger guards `DELETE` too;
  this one must not. `analysis_sessions → analyses` is `ON DELETE CASCADE` and
  spec §54 makes expiry the default, so guarding `DELETE` would make the
  retention sweep impossible.
* **NULL → value passes.** Every column starts empty and is written once, by
  the stage that resolves it. Refusing all change would refuse the only write.
* **`IS DISTINCT FROM`.** Rewriting the same value is a no-op rather than a
  violation, so a retried transaction is safe.

`status`, `card_id` and `completed_at` go on moving for the life of the
analysis; the `WHEN` clause means the function is not called for an ordinary
transition at all.

Alembic compares no triggers, so nothing will warn if this and
`tcg_api/analysis/tables.py` drift apart. `test_analysis_schema.py` asserts the
refusal against a real database, and that test is the only guard there is.

Refs: M2, spec §57, §12, §54
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "7e4a90c15db3"
down_revision: str | None = "5c1f83a4b6d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: `TEXT COLLATE "C"`, matching `analysis_sessions.application_version` and every
#: other printed identifier in this schema: a version is compared for equality
#: and ordered, and a database's locale must not change what either means.
PRINTED = sa.Text(collation="C")

#: The six §57 columns the trigger guards. Written out rather than imported from
#: `tables.py`: a migration is a snapshot of what was applied, and must not
#: change when that module does.
REPRODUCIBILITY_COLUMNS = (
    "application_version",
    "model_bundle_version",
    "card_database_version",
    "grading_rules_version",
    "market_snapshot_id",
    "economic_configuration_id",
)

_CHANGED = "\n      OR ".join(
    f"(OLD.{column} IS NOT NULL AND NEW.{column} IS DISTINCT FROM OLD.{column})"
    for column in REPRODUCIBILITY_COLUMNS
)

# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: the same statement is declared through `sa.DDL` in `tables.py`, which
# runs it through Python's `%` interpolation, so a format specifier in the body
# fails there at compile time. Kept identical in both places.
IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION analyses_reproducibility_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE USING
        ERRCODE = 'restrict_violation',
        MESSAGE = 'the reproducibility record of analysis ' || OLD.id
                  || ' is immutable and was already written',
        HINT    = 'Run a new analysis rather than rewriting an old one.';
END;
$$;
"""

IMMUTABLE_TRIGGER = f"""
CREATE TRIGGER trg_analyses_reproducibility_immutable
BEFORE UPDATE ON analyses
FOR EACH ROW
WHEN ({_CHANGED})
EXECUTE FUNCTION analyses_reproducibility_is_immutable();
"""


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "application_version",
            PRINTED,
            nullable=True,
            comment=(
                "The version of this service that ran the analysis, resolved when the "
                "job claimed it. Not a duplicate of "
                "analysis_sessions.application_version: a session lives for days and a "
                "deployment can happen inside one, so the version that opened the "
                "session is not necessarily the version that produced the result. Spec "
                "§57 wants the one that produced it. NULL until a run has claimed the "
                "analysis."
            ),
        ),
    )
    op.add_column(
        "analyses",
        sa.Column(
            "card_database_version",
            PRINTED,
            nullable=True,
            comment=(
                "Which published card_database_versions.version was current when the "
                "analysis ran — the identifier itself, resolved at execution time, "
                "never a pointer to whatever is current now. No foreign key: a version "
                "record must be able to outlive nothing and an analysis must not be "
                "blocked by one, and the identifier is the fact worth keeping. NULL "
                "when no catalog version had been published, which is a fact rather "
                "than a gap."
            ),
        ),
    )
    op.add_column(
        "analyses",
        sa.Column(
            "grading_rules_version",
            PRINTED,
            nullable=True,
            comment=(
                "Which grading-rule version the prediction was made under — spec §57. "
                "Always NULL in V1: no grading rules exist yet, and the column is here "
                "so the absence is documented rather than indistinguishable from a bug "
                "when they do. The milestone that introduces them fills it at run time."
            ),
        ),
    )
    op.create_table_comment(
        "analyses",
        "One analysis of one card — spec §12, plus spec §57's reproducibility record. "
        "The six version columns are write-once: a trigger refuses to change one that "
        "already holds a value, so a re-run is a new analysis rather than an edit.",
        existing_comment=(
            "One analysis of one card — spec §12. Its reproducibility record is only "
            "partly here: §57's card database and grading rules versions are their own "
            "issue."
        ),
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(IMMUTABLE_TRIGGER)


def downgrade() -> None:
    # The trigger first and the function separately: dropping the columns would
    # take the trigger with them but leave the function behind, which is the
    # orphan the card-database-version revision documents.
    op.execute("DROP TRIGGER IF EXISTS trg_analyses_reproducibility_immutable ON analyses")
    op.execute("DROP FUNCTION IF EXISTS analyses_reproducibility_is_immutable()")
    op.create_table_comment(
        "analyses",
        "One analysis of one card — spec §12. Its reproducibility record is only "
        "partly here: §57's card database and grading rules versions are their own "
        "issue.",
        existing_comment=(
            "One analysis of one card — spec §12, plus spec §57's reproducibility "
            "record. The six version columns are write-once: a trigger refuses to "
            "change one that already holds a value, so a re-run is a new analysis "
            "rather than an edit."
        ),
    )
    op.drop_column("analyses", "grading_rules_version")
    op.drop_column("analyses", "card_database_version")
    op.drop_column("analyses", "application_version")
