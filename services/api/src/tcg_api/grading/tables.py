"""Spec §23's `grading_rules`, as SQLAlchemy Core.

One table: one published grading standard, of one company, at one version.
`packages/grading-companies` holds the same record as a frozen dataclass
(`tcg_grading_companies.GradingRules`) and three module constants; this is where
those constants go so that an analysis run six months ago can still resolve the
exact version it recorded in `analyses.grading_rules_version`.

**Core, not ORM**, and the entity is not redeclared here: `tcg_api.grading.rules`
reads rows and constructs `GradingRules`, which validates on construction. Same
direction as the catalog adapter, no magic.

**The rules body is `{}` in V1, by decision rather than by omission.** Each
company's grading standard is that company's copyrighted text and this
repository does not reproduce it. What §57 needs is the *identifier* — so a run
made today can be told apart from one made after a company revises its standard
— plus a source a human can open. Both are columns here. A future milestone that
genuinely needs machine-readable tolerances publishes a new version carrying
them; the JSONB column is already there for it.

One departure from §23's column list, and it is the whole design:

* **`effective_to` is derived, not stored.** A version is in force from its
  `effective_from` until the next version of the same company begins — the
  intervals are `[fᵢ, fᵢ₊₁)` by construction, so two of one company's ranges
  *cannot* overlap and the only ambiguity left is two rows sharing a start,
  which `uq_grading_rules_company_effective_from` refuses. Storing the column
  instead would mean a superseding version has to UPDATE its predecessor, which
  carves an exception into the immutability the acceptance criterion asks for;
  and without `btree_gist` — which this schema does not install, and
  `test_catalog_schema.py` asserts it does not — there is no EXCLUDE constraint,
  so two overlapping *closed* ranges would stay representable. Nothing
  downstream can tell: `tcg_api.grading.rules` computes `effective_to` with
  `lead()` and every `GradingRules` it returns carries it.

  ponytail: a standard *retired with no successor* is therefore not
  representable. No grading company has done that — retiring the standard means
  it stopped grading — and if one does, the fix is additive: an
  `effective_to DATE NULL` column and `COALESCE(effective_to, lead(...))` in the
  resolver. Do not pre-build it.

Like `card_database_versions`, this table is append-only and says so in the
database rather than in a comment — see the trigger at the foot of this module.
"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from tcg_api.tables import NO_METADATA as _NO_METADATA
from tcg_api.tables import PRINTED as _PRINTED
from tcg_api.tables import metadata

__all__ = ["TABLES", "grading_rules"]


grading_rules = sa.Table(
    "grading_rules",
    metadata,
    sa.Column(
        "version",
        _PRINTED,
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
        _PRINTED,
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
        server_default=_NO_METADATA,
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
        server_default=sa.func.now(),
        nullable=False,
        comment=(
            "When this row was written. Distinct from `verified_on`, which is when "
            "the standard itself was read — the same pair `card_database_versions` "
            "draws between `created_at` and `generated_at`."
        ),
    ),
    # `version` is the primary key, and there is no surrogate id.
    # `card_database_versions` carries one and admits in its own comment that
    # "the record's identity is `version`"; here nothing references this table
    # by key at all — `analyses.grading_rules_version` deliberately has no
    # foreign key, because an analysis must resolve the standard it used whether
    # or not the row is still reachable — and `GradingRules` has no id field. A
    # uuid column would need a namespace constant and an id helper serving
    # nothing.
    sa.PrimaryKeyConstraint("version", name="pk_grading_rules"),
    # This *is* the non-overlap enforcement, not a supplement to it. Given
    # derived `effective_to`, one company's intervals can only overlap by two
    # rows sharing a start — which this refuses. `NULLS NOT DISTINCT` is the
    # load-bearing half: without it a company could carry two undated standards
    # and "which was in force" would have no answer. PostgreSQL 15+; both
    # Compose and CI run 17.
    #
    # The index it brings is also exactly what the resolver's
    # `PARTITION BY company ORDER BY effective_from` reads, so there is no
    # further index. Three rows.
    sa.UniqueConstraint(
        "company",
        "effective_from",
        name="uq_grading_rules_company_effective_from",
        postgresql_nulls_not_distinct=True,
    ),
    # No CHECK on `company`, and this is the one place `tcg_api.tables`'s
    # `one_of` looks applicable and is not. `GradingCompany` is a vocabulary
    # rather than a closed set precisely so that §22's "a fourth company costs
    # one new adapter and no caller change" stays true; a CHECK built from it
    # would make a fourth company cost a migration of this table as well.
    # `market_observations.grading_company` (#50) *does* take one — a price row
    # is data about a company V1 ships, where this is the company's own record.
    #
    # No CHECK relating `verified_on` to `effective_from` either: a standard
    # announced today to take effect next quarter is legitimate.
    comment=(
        "One published grading standard, of one company, at one version — spec §23. "
        "Append-only, enforced by trg_grading_rules_immutable. `effective_to` is "
        "derived rather than stored; see tcg_api.grading.tables."
    ),
)


TABLES: Final = (grading_rules,)


# ---------------------------------------------------------------------------
# Immutability, as a database guarantee rather than a promise
# ---------------------------------------------------------------------------
# §23: "Do not overwrite old grading rules. Historical analyses must retain the
# rules version used." A well-meaning correction that UPDATEs a row destroys
# that permanently and silently — every analysis that recorded the version now
# resolves text it never saw. A correction is a new version with a new
# `effective_from`, never an edit, and the database is what enforces it.
#
# Flat `BEFORE UPDATE OR DELETE`, with no `WHEN` clause and no `IS DISTINCT
# FROM` escape — deliberately unlike `trg_analyses_reproducibility_immutable`,
# which permits NULL → value because `analyses` rows are filled in by later
# stages. A `grading_rules` row is written complete, by a seed, from a constant.
# There is no second write to permit.
#
# Three things worth knowing before changing this:
#
# * `plpgsql` is not an extension this schema installs. It ships enabled in
#   every stock PostgreSQL and in Supabase, and no CREATE EXTENSION is issued.
# * TRUNCATE bypasses row-level triggers, deliberately. That is what lets the
#   integration fixtures reset between tests; the trigger guards the mutation
#   path application code can actually reach.
# * Alembic's `compare_metadata` does not compare triggers, so nothing will warn
#   if this and the migration drift apart. `test_grading_schema.py` asserts an
#   UPDATE and a DELETE are refused against a real database; that test is the
#   only guard there is.
# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: `sa.DDL` runs its statement through Python's `%` interpolation, so a
# format specifier in the body fails at compile time rather than at runtime.
# Do not "simplify" this back into the printf form.
def _ddl(statement: str) -> sa.DDL:
    """`sa.DDL` is unannotated in SQLAlchemy's own types, and mypy runs strict here.

    One ignore in one place rather than four scattered through the statements
    below, where they would read as though something about each was doubtful.
    """
    return sa.DDL(statement)  # type: ignore[no-untyped-call]


_IMMUTABLE_FUNCTION: Final = _ddl(
    """
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
)

_IMMUTABLE_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_grading_rules_immutable
    BEFORE UPDATE OR DELETE ON grading_rules
    FOR EACH ROW EXECUTE FUNCTION grading_rules_are_immutable();
    """
)

# Two statements, two DDL objects: the asyncpg driver prepares each statement it
# is handed, and a prepared statement may not contain more than one.
_DROP_IMMUTABLE_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_grading_rules_immutable ON grading_rules"
)

_DROP_IMMUTABLE_FUNCTION: Final = _ddl("DROP FUNCTION IF EXISTS grading_rules_are_immutable()")

sa.event.listen(
    grading_rules,
    "after_create",
    _IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
sa.event.listen(
    grading_rules,
    "after_create",
    _IMMUTABLE_TRIGGER.execute_if(dialect="postgresql"),
)
sa.event.listen(
    grading_rules,
    "before_drop",
    _DROP_IMMUTABLE_TRIGGER.execute_if(dialect="postgresql"),
)
sa.event.listen(
    grading_rules,
    "before_drop",
    _DROP_IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
