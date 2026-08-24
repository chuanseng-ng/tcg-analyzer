"""add the versioned grading rules schema

Spec §23 requires published grading rules to be stored separately from learned
model behaviour, versioned, and never overwritten — "historical analyses must
retain the rules version used". `analyses.grading_rules_version` has existed
since the reproducibility record landed and nothing writes it, because there was
nothing to point at. This is the thing it points at.

The shape and the reasoning live in
`services/api/src/tcg_api/grading/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Four things worth knowing before reading the DDL:

* **`effective_to` is derived, not stored, and that is the whole design.** §23
  names the column; a version is in force from its `effective_from` until the
  next version of the same company begins, so the intervals are `[fᵢ, fᵢ₊₁)` by
  construction and two of one company's ranges *cannot* overlap. The reader
  computes it:

      SELECT ..., lead(effective_from) OVER (
                      PARTITION BY company ORDER BY effective_from ASC NULLS FIRST
                  ) AS effective_to
      FROM grading_rules

  Storing it instead would mean a superseding version has to UPDATE its
  predecessor, carving an exception into the immutability the acceptance
  criterion asks for; and without `btree_gist` — which this schema does not
  install — there is no EXCLUDE constraint, so two overlapping *closed* ranges
  would stay representable.
* **`uq_grading_rules_company_effective_from` IS the non-overlap constraint**,
  not a supplement to it: given derived ranges, the only overlap possible is two
  rows sharing a start. `NULLS NOT DISTINCT` is the load-bearing half — TAG and
  BGS publish no effective date at all, and without it a company could carry two
  undated standards with no answer to which was in force. PostgreSQL 15+.
* **No CHECK on `company`.** `GradingCompany` is a vocabulary rather than a
  closed set precisely so §22's "a fourth company costs one new adapter and no
  caller change" stays true; a CHECK built from it would make a fourth company
  cost a migration of this table too.
* **Immutability is a trigger**, flat `BEFORE UPDATE OR DELETE` with no `WHEN`
  clause — unlike `trg_analyses_reproducibility_immutable`, which permits
  NULL → value because those rows are filled in by later stages. A
  `grading_rules` row is written complete, from a constant. `plpgsql` is not an
  extension this schema installs, and `TRUNCATE` bypasses row triggers, which is
  what lets the integration fixtures reset.

No seed rows are inserted here. They would need either an import of
`tcg_grading_companies` — forbidden in a migration, which must not change when
that package does — or a third literal copy of records that already exist twice.
`uv run tcg-seed-grading-rules` writes them.

Alembic compares no triggers, so `compare_metadata` will not notice if this and
`tables.py` drift apart. `services/api/tests/test_grading_schema.py` asserts an
UPDATE and a DELETE are actually refused; that test is the only guard there is.

Revision ID: 50c399cb7b9b
Revises: 1f6a2c8b40de
Create Date: 2026-08-24 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "50c399cb7b9b"
down_revision: str | None = "1f6a2c8b40de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text, ordered by byte rather than by whatever locale the server was
# initialised with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

NO_RULES = sa.text("'{}'::jsonb")

# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: the same statement is declared through `sa.DDL` in `tables.py`, and
# `sa.DDL` runs its statement through Python's `%` interpolation, so a format
# specifier in the body fails at compile time. Kept identical here so the two
# copies can be diffed by eye.
#
# `restrict_violation` is SQLSTATE class 23, which SQLAlchemy surfaces as an
# `IntegrityError` — the same shape as every other constraint in this schema,
# rather than an `InternalError` a caller would have to special-case.
IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION grading_rules_are_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE USING
        ERRCODE = 'restrict_violation',
        MESSAGE = 'grading_rules is immutable: '
                  || TG_OP || ' on ' || OLD.version || ' was refused',
        HINT    = 'Publish a new version rather than rewriting one.';
END;
$$;
"""

IMMUTABLE_TRIGGER = """
CREATE TRIGGER trg_grading_rules_immutable
BEFORE UPDATE OR DELETE ON grading_rules
FOR EACH ROW EXECUTE FUNCTION grading_rules_are_immutable();
"""


def upgrade() -> None:
    op.create_table(
        "grading_rules",
        sa.Column(
            "version",
            PRINTED,
            nullable=False,
            comment=(
                "The identifier a historical analysis retains — 'psa-rules-2026-08-24'. "
                "This is the record's identity, and what `analyses.grading_rules_version` "
                "names (spec §57). Never reused and never edited: a revised standard is a "
                "new row. No company publishes a version of its own, so the identifier is "
                "this repository's, date-stamped — ADR 0006's answer for `data_version`, "
                "reused."
            ),
        ),
        sa.Column(
            "company",
            PRINTED,
            nullable=False,
            comment=(
                "The company's lowercase slug — 'psa', 'tag', 'bgs'. `COLLATE \"C\"` so "
                "that equality and the resolver's PARTITION BY mean the same thing on the "
                "C-locale Compose database and on CI's default-locale one."
            ),
        ),
        sa.Column(
            "effective_from",
            sa.Date(),
            nullable=True,
            comment=(
                "When the company's published standard took effect, as far as it states "
                "one. PSA says 2008-02-01; TAG and BGS state none at all, and record NULL "
                "rather than a date guessed from a copyright footer. NULL reads as 'in "
                "force since before this repository began recording' — it matches every "
                "date, and the resolver returns it as NULL rather than inventing one."
            ),
        ),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            comment=(
                "§23's source/provenance: where the standard was read, as a URL a human "
                "can open. Plain TEXT — never ordered, compared or prefix-matched."
            ),
        ),
        sa.Column(
            "verified_on",
            sa.Date(),
            nullable=False,
            comment=(
                "When the source was last read. ADR 0006's ninety-day rule applies to "
                "reference data too, and this is the field it applies to: a record older "
                "than ninety days is re-read before anything relies on it."
            ),
        ),
        sa.Column(
            "rules",
            postgresql.JSONB(),
            server_default=NO_RULES,
            nullable=False,
            comment=(
                "§23's machine-readable rules body. Empty in V1 by decision — the "
                "published standards are the companies' copyrighted text and this "
                "repository does not reproduce them; see the module docstring. The column "
                "exists so a future version can carry tolerances without a migration."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=(
                "When this row was written. Distinct from `verified_on`, which is when "
                "the standard itself was read — the same pair `card_database_versions` "
                "draws between `created_at` and `generated_at`."
            ),
        ),
        # No surrogate id: nothing references this table by key, and
        # `GradingRules` has no id field.
        sa.PrimaryKeyConstraint("version", name="pk_grading_rules"),
        sa.UniqueConstraint(
            "company",
            "effective_from",
            name="uq_grading_rules_company_effective_from",
            postgresql_nulls_not_distinct=True,
        ),
        comment=(
            "One published grading standard, of one company, at one version — spec §23. "
            "Append-only, enforced by trg_grading_rules_immutable. `effective_to` is "
            "derived rather than stored; see tcg_api.grading.tables."
        ),
    )

    op.execute(IMMUTABLE_FUNCTION)
    op.execute(IMMUTABLE_TRIGGER)


def downgrade() -> None:
    # `DROP TABLE` would take the trigger with it, but not the function. Naming
    # all three keeps the reversal exact and leaves nothing orphaned in the
    # catalog for the next `upgrade` to collide with.
    op.execute("DROP TRIGGER IF EXISTS trg_grading_rules_immutable ON grading_rules")
    op.drop_table("grading_rules")
    op.execute("DROP FUNCTION IF EXISTS grading_rules_are_immutable()")
