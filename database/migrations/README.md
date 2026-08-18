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
- **Declare every table in code, not only in a migration.** `env.py` reads
  `target_metadata` from `services/api/src/tcg_api/catalog/tables.py`, so
  `alembic revision --autogenerate` compares a database against what that module
  declares — and proposes dropping anything it does not find there. A migration
  is still written and reviewed by hand; autogenerate is a starting point, not
  an author.

## Current state

Two revisions.

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

The remaining domain tables — `analyses`, `images`, `market_observations` —
arrive in their own milestones.
