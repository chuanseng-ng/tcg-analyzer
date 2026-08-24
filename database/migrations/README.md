# `database/migrations`

Alembic migrations. Every schema change arrives through a reviewed, versioned
migration — never ad-hoc DDL.

## Layout

| Path | Contents |
| --- | --- |
| `env.py` | Async Alembic environment; reads `TCG_API_DATABASE_URL` |
| `script.py.mako` | Template every generated revision is rendered from |
| `versions/` | The revisions themselves |
| `../../alembic.ini` | Configuration, at the repository root |

Revision filenames are `YYYYMMDD_HHMMSS_<rev>_<slug>.py`, stamped in UTC, so the
directory listing and the revision graph agree on the order.

## Commands

Run from the repository root, with `TCG_API_DATABASE_URL` set — see the
Database section of the root `README.md`.

```bash
uv run alembic upgrade head              # apply everything outstanding
uv run alembic downgrade -1              # revert the most recent revision
uv run alembic revision -m "description" # write a new revision by hand
uv run alembic current                   # which revision is applied
```

## Conventions

- **Plain PostgreSQL only.** No extensions, nothing Supabase-specific. Spec §8
  keeps the deployment platform open between ordinary PostgreSQL and Supabase,
  and that option only survives if the schema history never depends on either.
- **Forward-only in spirit.** `downgrade()` is written and must work — the
  harness test exercises it — but the history is a record, not a scratchpad.
  Fix a mistaken migration with a new revision rather than by editing a
  revision that has already been applied anywhere.
- **One capability per migration**, matching the one-capability-per-PR rule.
- **No credentials.** The URL lives in the environment, never in `alembic.ini`.
- **A named constraint takes its short name.** The `MetaData`'s naming
  convention supplies the `ck_<table>_` prefix, and Alembic applies that same
  convention to `op.create_table`, so passing the rendered name in a migration
  renders it twice.
- **Declare every table in code, not only in a migration.** `env.py` reads
  `target_metadata` from `services/api/src/tcg_api/table_registry.py`, which
  imports each domain's table module — `tcg_api/catalog/tables.py` and
  `tcg_api/analysis/tables.py`. Importing them is what attaches their tables to
  the `MetaData` those modules share, so the same `MetaData` read straight from
  `tcg_api/tables.py` would be empty. `alembic revision --autogenerate` compares a
  database against what the modules declare and proposes dropping anything it does
  not find there, so a new domain is registered in the registry — one place, not
  in every caller that needs the whole schema. A migration is still written and
  reviewed by hand; autogenerate is a starting point, not an author.

## Current state

Four revisions.

`0255d9f37125` is the baseline: `migration_harness_check`, a single-column table
whose only job was to prove the harness applies and reverts cleanly against real
PostgreSQL. Its `COMMENT ON TABLE` said it would be dropped by the migration
that introduced the first domain table, and it was.

`0d60d1982d83` is that migration — spec §10's card catalog: `sets`, `cards` and
`card_external_ids`. Three things about it are load-bearing and should survive
any later change:

- **No provider column on `cards`.** Several external databases may point at one
  canonical card, so provider identifiers live only in `card_external_ids`.
- **`game` is a column.** Nothing hard-codes Pokémon; a second TCG is rows of
  data (spec §73).
- **Printed text is `COLLATE "C"`.** The local database is initialised
  `--locale=C` and CI's PostgreSQL service inherits the image default, so a
  column that took the server's collation would sort — and index `LIKE 'x%'` —
  differently in the two.

`downgrade` restores the harness table, so the history reverses exactly.

`352eb3d5e889` adds `card_database_versions` — spec §57's
`card_database_version`, one immutable row per import run. Three things about it
are load-bearing:

- **`ordinal` is the order, and the identifier is not.** Under `COLLATE "C"`
  `pokemon-catalog-v0.10.0` sorts *before* `v0.3.0`, so resolving "current" by
  ordering on the version string would answer with the older catalog. It is
  `GENERATED ALWAYS AS IDENTITY`, so no import can place itself out of sequence.
- **A trigger enforces immutability.** `BEFORE UPDATE OR DELETE`, raising
  `restrict_violation` so it reaches a caller as an `IntegrityError` like every
  other constraint here. `plpgsql` is **not** an extension this schema installs —
  it ships enabled in every stock PostgreSQL and in Supabase, and no `CREATE
  EXTENSION` is issued, so the rule above holds. `TRUNCATE` bypasses row-level
  triggers, which is what lets the integration fixtures reset between tests.
- **No foreign key into `cards`.** A version records that a run happened, not
  which rows survived it, and it has to outlive every row it counted.

Alembic compares no triggers, so `compare_metadata` will not notice if a trigger
and `tables.py` drift apart. `services/api/tests/test_catalog_schema.py` asserts
an `UPDATE` and a `DELETE` are actually refused; that test is the only guard.

`29d14fe0fcee` adds the analysis spine — spec §12's `analysis_sessions` and
`analyses`, and §11's `images`. Nothing writes to them yet; the upload endpoint,
the state machine and the confirmation are separate issues. Four things about it
are load-bearing:

- **All six `side` values from day one.** V1 writes `front` and `back`; §52's
  guided photography adds angled and surface-lit captures, and §52 requires that
  the V1 pipeline already be compatible with them. Admitting them costs a longer
  CHECK constraint. Adding them later would cost a migration against a table full
  of user photographs.
- **A vocabulary is a CHECK, not a `CREATE TYPE`.** PostgreSQL has no
  `ALTER TYPE ... DROP VALUE`, so a revision that added a state would not be
  reversible — and `DROP TABLE` leaves a type behind exactly as it leaves a
  trigger's function behind. A named CHECK reverses with its table and names
  itself in the `IntegrityError` a caller sees. **Alembic compares a check
  constraint's name and not its text**, so the drift guard cannot notice an `IN`
  list that has diverged from `tables.py`; `test_analysis_schema.py` inserts every
  value of every vocabulary, and that is what closes the gap.
- **`expires_at` plus `ON DELETE CASCADE` is spec §54's retention mechanism.**
  Expiry is the default and retention the exception, which only has teeth if the
  column is `NOT NULL` from the first migration; the cascade then makes expiring
  a session one statement. **Cascading the rows does not delete the objects** —
  the retention job must read `original_uri` and `normalized_uri` before the
  `DELETE`, or every expired photograph is orphaned in object storage.
- **Spec §57's reproducibility record is six columns on `analyses`, and is
  immutable.** `application_version`, `card_database_version` and
  `grading_rules_version` join the three §12 already listed;
  `trg_analyses_reproducibility_immutable` fires `BEFORE UPDATE` and refuses to
  change any of them that already holds a value, so a re-run is a new analysis
  rather than an edit. `UPDATE` **only** — `analysis_sessions → analyses` is
  `ON DELETE CASCADE`, so guarding `DELETE` as `card_database_versions` does
  would make the retention sweep above impossible. NULL → value passes, since
  every column is filled once by the stage that resolves it, and
  `IS DISTINCT FROM` keeps a replayed write a no-op rather than a failure.
  Alembic compares no triggers, so `test_analysis_schema.py`'s refusal tests are
  the only guard against this drifting from `tables.py`.

`50c399cb7b9b` adds spec §23's `grading_rules`: one published grading standard,
of one company, at one version. It is what `analyses.grading_rules_version` has
been pointing at nothing since the reproducibility record landed. Three things
about it are load-bearing:

- **`effective_to` is derived rather than stored, and that is the whole design.**
  §23 names the column; a version is in force from its `effective_from` until the
  next version of the same company begins, so one company's intervals are
  `[fᵢ, fᵢ₊₁)` by construction and two of them *cannot* overlap.
  `tcg_api.grading.rules` computes it with `lead()`, so every record a caller
  receives still carries one. Storing it instead would mean a superseding version
  has to `UPDATE` its predecessor — an exception carved into the immutability §23
  asks for — and without `btree_gist`, which this schema does not install, there
  would be no `EXCLUDE` constraint to stop two overlapping *closed* ranges.
- **`uq_grading_rules_company_effective_from` IS the non-overlap constraint**,
  not a supplement to it, and `NULLS NOT DISTINCT` is its load-bearing half. TAG
  and BGS publish no effective date at all; without it a company could carry two
  undated standards and "which was in force" would have no answer.
- **No CHECK on `company`**, unlike every other vocabulary in this schema.
  `GradingCompany` is deliberately a vocabulary rather than a closed enum so that
  §22's "a fourth company costs one new adapter and no caller change" stays true;
  a CHECK built from it would make a fourth company cost a migration here too.
  `market_observations.grading_company` takes one for the opposite reason — a
  price row is data *about* a company V1 ships.

Immutability is the second `BEFORE UPDATE OR DELETE` trigger in this schema, and
flat: no `WHEN` clause and no `IS DISTINCT FROM` escape, because a
`grading_rules` row is written complete from a constant rather than filled in by
later stages the way `analyses` is. Alembic compares no triggers, so
`services/api/tests/test_grading_schema.py` is the only guard.

The remaining domain table — `market_observations` — arrives in its own
milestone.
