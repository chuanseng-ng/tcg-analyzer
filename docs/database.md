# The database

PostgreSQL, reached through SQLAlchemy's asyncpg driver and migrated with
Alembic.

Every schema change arrives through a reviewed, versioned Alembic migration —
never ad-hoc DDL. Start PostgreSQL, then migrate:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait

export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg

uv run alembic upgrade head              # migrate up
uv run alembic downgrade -1              # migrate down one revision
uv run alembic revision -m "description" # new revision
uv run alembic current                   # which revision is applied
uv run alembic history                   # the revision graph
```

Tear the database down with `docker compose -f
infrastructure/local/docker-compose.yml down -v`. The `-v` discards the volume,
so the next `up` starts from an empty database and `upgrade head` rebuilds the
schema from the migrations alone.

`TCG_API_DATABASE_URL` is a SQLAlchemy URL using the asyncpg driver:

```text
postgresql+asyncpg://<user>:<password>@<host>:<port>/<database>
```

It is the single source of the connection string — the API service and Alembic
both read it, and it never appears in `alembic.ini`. Alembic reads the
environment only, so export it as above even when `.env` already carries it for
the API. The value in `.env.example` matches the Compose defaults: local-only
values, not secrets; see `infrastructure/local/README.md`. A real credential
belongs in `.env`, which is not committed.

Migrations are portable, plain PostgreSQL. Nothing may depend on a
Supabase-specific feature (spec §8), and no extension is required.

The schema's source of truth is the `MetaData` in
`services/api/src/tcg_api/tables.py`, which `database/migrations/env.py` reads as
`target_metadata`. The tables themselves are declared per domain — the card
catalog in `tcg_api/catalog/tables.py`, the analysis spine in
`tcg_api/analysis/tables.py`, the published grading standards in
`tcg_api/grading/tables.py` and the market data in `tcg_api/market/tables.py` —
and `tcg_api/table_registry.py` imports them all,
which is what makes that `MetaData` complete. `env.py` reads it from the registry
for exactly that reason. Declare a new table in one of those modules as well as in
its migration, and register a new domain in the registry, or
`alembic revision --autogenerate` will propose dropping it.

Once the schema is up, load the hand-authored card catalog fixtures:

```bash
uv run tcg-seed-catalog
```

Roughly twenty English and Japanese cards under a `manual` provider, enough to
search, identify and price against. It is idempotent, so re-run it after editing
a fixture; see `database/seeds/README.md`. These fixtures are the catalog a
developer gets without a network, and
[ADR 0004](adr/0004-the-canonical-card-catalog-source.md) keeps them as the
floor if the TCGdex position ever has to be withdrawn.

The published grading standards are seeded separately:

```bash
uv run tcg-seed-grading-rules
```

One row per published PSA, TAG and BGS standard (spec §23), written into
`grading_rules` from the versions `tcg_grading_companies` carries. It is
idempotent, and a published version is never rewritten regardless of what the
loader asks for — the database refuses the update. `GET /grading-companies`
reads its `rules` from this table and each grade scale from the package, so a
deployment that has not run this still serves all three scales and reports
`rules` as null.

For a real catalog, import one from TCGdex:

```bash
uv run tcg-import-catalog --version pokemon-catalog-tcgdex-v0.1.0 --language en --language ja
```

Two phases in one command. It fetches the source into a *snapshot* — three
JSON files under `.catalog-snapshots/tcgdex`, gitignored — and then loads that
snapshot into the database in a single transaction. The split is not
decoration: rarity and printing variants come only from TCGdex's per-card
endpoint, so a full English-and-Japanese import is roughly 36,000 requests. A
snapshot is a reviewable artifact, it carries a `sha256` digest that a later
load verifies, and it can be replayed exactly.

```bash
uv run tcg-import-catalog --version pokemon-catalog-tcgdex-v0.1.0 --language en --set base1
uv run tcg-import-catalog --from-snapshot .catalog-snapshots/tcgdex
```

The first narrows the run to one set, which is how to check a change in seconds
rather than an hour. The second loads an existing snapshot and uses no network
at all. `--cache-dir` keeps raw card payloads so an interrupted full run resumes
instead of starting over, and `--fetch-only` writes the snapshot without
touching a database. A TCGdex set id belongs to one language — `base1` is
English, `SV2a` is Japanese — so `--set` imports from whichever `--language` has
it, and a set found in none of them is an error rather than a silent no-op.

`--version` is required and is never reused. Two imports are two versions: the
rows they write converge, the records of the runs accumulate.

The catalog is versioned. Every run that writes it — the seed loader and the
import above — publishes an immutable `card_database_version` recording the
identifier, the source, the licence relied upon, the upstream revision and the
record counts. That is one of the seven fields spec §57 requires an analysis to
keep so it can be re-derived rather than re-guessed, and `GET /catalog/version`
is how a client reads it. No card images are imported: TCGdex's MIT licence
covers its compilation, not The Pokémon Company's artwork.

Published versions are never rewritten: a database trigger refuses `UPDATE` and
`DELETE` outright, and a re-import publishes a new version rather than editing an
old one. Identifiers are explicit and ordered — `pokemon-catalog-v0.3.0`, never
`/latest/`.

Tests that need a live database are marked `integration` and skip when
`TCG_API_DATABASE_URL` is unset, so the default suite never needs Docker:

```bash
uv run pytest -m integration   # requires PostgreSQL to be running
uv run pytest -m "not integration"
```

The catalog import is tested against recorded payloads, so it needs no network
either. One test does reach `api.tcgdex.net`, to notice when the source changes
shape; it is marked `network`, deselected in CI, and run by hand after changing
`services/api/src/tcg_api/catalog/tcgdex.py`:

```bash
uv run pytest -m network
```
