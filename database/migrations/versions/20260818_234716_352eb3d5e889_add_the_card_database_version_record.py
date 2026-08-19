"""add the card database version record

Spec §57 requires every analysis to record seven things so it can be re-derived
rather than re-guessed, and one of them is `card_database_version` — which
catalog the answer was computed against. This is that record: one immutable row
per import run, carrying the identifier, the source, the licence relied upon,
the upstream revision, when the data was made, and how much of it there was.
ADR 0004 already committed every import to writing exactly these fields here,
"no competing provenance table".

The shape and the reasoning live in
`services/api/src/tcg_api/catalog/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Four things worth knowing before reading the DDL:

* **`ordinal` is the order, and the identifier is not.** Under `COLLATE "C"`,
  'pokemon-catalog-v0.10.0' sorts *before* 'pokemon-catalog-v0.3.0', so ordering
  on the version string would resolve "current" to the older catalog the moment a
  minor version reaches double digits. The current version is
  `ORDER BY ordinal DESC LIMIT 1`. The semantic version inside the identifier is
  for humans; publication order is for the database.
* **`GENERATED ALWAYS AS IDENTITY`, so no import can place itself out of
  sequence.** Supplying a value requires `OVERRIDING SYSTEM VALUE`, which is
  exactly the deliberate act it should be.
* **No foreign key into `cards`.** A version records that a run happened, not
  which rows survived it — and it has to outlive every row it counted, because
  that is what a historical analysis needs from it.
* **Immutability is a trigger.** A published version is never rewritten, and
  convention cannot deliver that: the next importer to reach for an `UPDATE`
  would break it silently. `plpgsql` is not an extension this schema installs —
  it ships enabled in every stock PostgreSQL and in Supabase, and no
  `CREATE EXTENSION` is issued, so the no-extensions rule holds. `TRUNCATE`
  bypasses row-level triggers, which is what lets the integration fixtures reset
  between tests.

Alembic compares no triggers, so `compare_metadata` will not notice if this and
`tables.py` drift apart. `services/api/tests/test_catalog_schema.py` asserts an
`UPDATE` and a `DELETE` are actually refused; that test is the only guard there
is.

Revision ID: 352eb3d5e889
Revises: 0d60d1982d83
Create Date: 2026-08-18 23:47:16.168633+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "352eb3d5e889"
down_revision: str | None = "0d60d1982d83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text, ordered by byte rather than by whatever locale the server was
# initialised with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

NO_METADATA = sa.text("'{}'::jsonb")

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
CREATE OR REPLACE FUNCTION card_database_versions_are_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE USING
        ERRCODE = 'restrict_violation',
        MESSAGE = 'card_database_versions is immutable: '
                  || TG_OP || ' on ' || OLD.version || ' was refused',
        HINT    = 'Publish a new version rather than rewriting one.';
END;
$$;
"""

IMMUTABLE_TRIGGER = """
CREATE TRIGGER trg_card_database_versions_immutable
BEFORE UPDATE OR DELETE ON card_database_versions
FOR EACH ROW EXECUTE FUNCTION card_database_versions_are_immutable();
"""


def upgrade() -> None:
    op.create_table(
        "card_database_versions",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="A surrogate key. The record's identity is `version`.",
        ),
        sa.Column(
            "ordinal",
            sa.BigInteger(),
            sa.Identity(always=True, start=1),
            nullable=False,
            comment=(
                "Publication order, assigned by the database. GENERATED ALWAYS so no "
                "writer can place a run out of sequence. The current version is the "
                "highest ordinal — a query, never a mutable /latest/ pointer (spec §31)."
            ),
        ),
        sa.Column(
            "version",
            PRINTED,
            nullable=False,
            comment=(
                "An explicit, ordered identifier — 'pokemon-catalog-v0.3.0'. Never a pointer."
            ),
        ),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            comment=("A lowercase slug naming where the data came from — 'tcgdex' or 'manual'."),
        ),
        sa.Column(
            "source_license",
            PRINTED,
            nullable=True,
            comment=(
                "The licence the source data arrived under — 'MIT' for TCGdex, which "
                "ADR 0004 requires travel with every import. NULL where the data is "
                "this project's own and no upstream licence applies."
            ),
        ),
        sa.Column(
            "source_revision",
            PRINTED,
            nullable=True,
            comment=(
                "The upstream revision imported — a git commit for TCGdex — so the "
                "import can be repeated exactly. NULL where the source has no revision."
            ),
        ),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "When the source data was produced or retrieved. Distinct from "
                "created_at: an import of last week's snapshot that runs today has "
                "the two a week apart, and only this one describes the data."
            ),
        ),
        sa.Column(
            "set_count",
            sa.Integer(),
            nullable=False,
            comment="How many sets the run wrote.",
        ),
        sa.Column(
            "card_count",
            sa.Integer(),
            nullable=False,
            comment="How many cards the run wrote.",
        ),
        sa.Column(
            "external_id_count",
            sa.Integer(),
            nullable=False,
            comment="How many provider identifiers the run wrote.",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=NO_METADATA,
            nullable=False,
            comment="Whatever the run recorded that has no column of its own.",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=("When this row was written. See generated_at for when the data was made."),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_card_database_versions"),
        sa.UniqueConstraint("version", name="uq_card_database_versions_version"),
        # An identity column hands out distinct values; it does not promise them
        # — a sequence can be restarted. This constraint is the promise, and it
        # is also the index `ORDER BY ordinal DESC LIMIT 1` reads to resolve the
        # current version.
        sa.UniqueConstraint("ordinal", name="uq_card_database_versions_ordinal"),
        # The short name, not the rendered one. Alembic applies
        # `target_metadata`'s naming convention to `op.create_table`, so passing
        # the full `ck_card_database_versions_...` here would render it twice.
        sa.CheckConstraint(
            "set_count >= 0 AND card_count >= 0 AND external_id_count >= 0",
            name="counts_are_not_negative",
        ),
        # No index beyond the two unique constraints bring with them. This table
        # holds one row per import run — tens of them — and every query it
        # serves is either the greatest ordinal or one exact identifier.
        comment=(
            "One import run, recorded once and never rewritten — spec §57's "
            "card_database_version. Append-only, enforced by "
            "trg_card_database_versions_immutable."
        ),
    )

    op.execute(IMMUTABLE_FUNCTION)
    op.execute(IMMUTABLE_TRIGGER)


def downgrade() -> None:
    # `DROP TABLE` would take the trigger with it, but not the function. Naming
    # all three keeps the reversal exact and leaves nothing orphaned in the
    # catalog for the next `upgrade` to collide with.
    op.execute(
        "DROP TRIGGER IF EXISTS trg_card_database_versions_immutable ON card_database_versions"
    )
    op.drop_table("card_database_versions")
    op.execute("DROP FUNCTION IF EXISTS card_database_versions_are_immutable()")
