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
- **Autogenerate is not available yet.** `target_metadata` is `None` because no
  models exist. M1 brings the first domain tables; point `target_metadata` at
  their `MetaData` then, or `alembic revision --autogenerate` will propose
  dropping every table it finds.

## Current state

One revision, the baseline: `migration_harness_check`. It is scaffolding, not
domain — a single-column table whose only job is to prove the harness applies
and reverts cleanly against real PostgreSQL. Its `COMMENT ON TABLE` says so in
the database itself. The migration that introduces the first domain table drops
it.

Domain tables — `cards`, `analyses`, `images`, `market_observations` — arrive in
their own milestones.
