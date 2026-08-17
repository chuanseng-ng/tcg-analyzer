# tcg-analyzer

TCG Grading Advisor — photograph an ungraded Pokémon card, front and back, and
get back its identity, its physical condition, a probability distribution over
grades for each grading company, market values, the economics of grading it, and
a recommendation on whether grading is worthwhile.

**This is not an official grading service.** It does not authenticate cards, and
its predictions are probabilities, never guaranteed grades.

> This README is a stub. The full overview, setup guide and architecture
> documentation arrive with #22.

## Repository layout

| Path | Contents |
| --- | --- |
| `apps/` | TypeScript applications — `web` (Next.js) and `annotation` |
| `services/` | Python services — `api`, `analysis`, `market-data`, `ingestion` |
| `ml/` | Python ML modules — detection, condition, per-company grading, evaluation |
| `packages/` | Shared Python libraries — domain, ports, economic engine |
| `database/` | Migrations, seeds and fixtures |
| `datasets/` | Dataset schemas, manifests and documentation — **never images** |
| `infrastructure/` | Docker, local development, deployment |
| `docs/` | Documentation and architecture decision records |
| `tests/` | Repository-level tests |

These are logical boundaries. They are not microservices in V1.

Every directory carries a `README.md` describing its responsibility and the
milestone that fills it.

## Toolchain

Two workspaces, split by language — see
[ADR 0001](docs/adr/0001-language-boundaries-in-the-monorepo.md).

| Workspace | Manager | Members |
| --- | --- | --- |
| TypeScript | pnpm (`pnpm-workspace.yaml`) | `apps/*` |
| Python | uv (`pyproject.toml`) | `packages/*`, `services/*`, `ml/*` |

### Prerequisites

- **Node** 20+ with pnpm. pnpm is pinned by the `packageManager` field and
  provisioned by Corepack: run `corepack enable pnpm` once, or prefix commands
  with `corepack` (`corepack pnpm install`) if enabling shims needs
  administrator rights.
- **[uv](https://docs.astral.sh/uv/)**. The Python version is pinned in
  `.python-version`; uv will fetch it.

### Commands

```bash
cp .env.example .env    # local configuration; `.env` is never committed
pnpm install            # resolve the TypeScript workspace
uv sync --all-packages  # resolve all Python workspace members
uv run pytest           # repository-level and per-package tests
```

### Configuration

Every variable the stack reads is documented in [`.env.example`](.env.example)
with a placeholder value. Copy it to `.env` and edit that — `.env` is
gitignored, and no credential is ever committed (the project may be
open-sourced). `tests/test_environment_example.py` fails if a setting exists in
code but not in the example, so the two cannot drift.

Configuration fails fast on a value that is wrong, and tolerates one that is
absent. A malformed `TCG_API_DATABASE_URL` — an unparseable URL, or a
synchronous driver where the engine is async — stops startup with a message
naming the variable. An *unset* one does not: the API starts, `/health`
answers, and `/readiness` reports `database: unavailable`, which is what a
fresh clone looks like before PostgreSQL is running.

The API's settings live in `services/api/src/tcg_api/config.py`; the web app's
in `apps/web/lib/env.ts`. Nothing else reads the environment directly, because
a variable only one module knows about is a variable that never reaches
`.env.example`.

#### Checks

CI runs exactly these, so a green run locally means a green run there. Nothing
in the pipeline is a step you cannot reproduce yourself.

```bash
uv run ruff check .                     # lint
uv run ruff format --check .            # formatting
uv run mypy packages/domain/src services/api/src
uv run pytest -m "not integration"      # integration tests need a database

pnpm --filter @tcg/web lint
pnpm --filter @tcg/web format:check
pnpm --filter @tcg/web gen:api-types:check   # frontend types match the schema
pnpm --filter @tcg/web typecheck
pnpm --filter @tcg/web test
```

If `gen:api-types:check` fails, the API's OpenAPI schema and the committed
frontend types have diverged. Regenerate and commit:

```bash
pnpm --filter @tcg/web gen:api-types
```

#### Web

```bash
pnpm install
pnpm --filter @tcg/web dev     # http://localhost:3000
pnpm --filter @tcg/web test
pnpm --filter @tcg/web build
```

The app reads `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`),
validated by `lib/env.ts`. Next reads env files from the app directory, so for
`pnpm dev` copy `apps/web/.env.example` to `apps/web/.env.local`; the root
`.env` is what the Compose stack passes to the container.

#### API

```bash
uv run uvicorn tcg_api.main:app --reload   # API on http://localhost:8000
```

`GET /health` reports the service status and the application version; the
OpenAPI schema is at `/openapi.json` and the interactive documentation at
`/docs`. Settings are read from `TCG_API_`-prefixed environment variables or
from `.env` — see [Configuration](#configuration).

The image is built from the repository root, because `services/api` is a member
of the uv workspace and cannot be resolved without it:

```bash
docker build -f infrastructure/docker/api.Dockerfile -t tcg-api:dev .
docker run --rm -p 8000:8000 tcg-api:dev
```

Compose wiring arrives with #20.
#### Database

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
Supabase-specific feature (spec §8).

Tests that need a live database are marked `integration` and skip when
`TCG_API_DATABASE_URL` is unset, so the default suite never needs Docker:

```bash
uv run pytest -m integration   # requires PostgreSQL to be running
uv run pytest -m "not integration"
```
