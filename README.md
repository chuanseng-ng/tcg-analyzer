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
pnpm install            # resolve the TypeScript workspace
uv sync --all-packages  # resolve all Python workspace members
uv run pytest           # repository-level and per-package tests
```

Build, lint and per-service commands are added as later M0 issues introduce
them.

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
both read it, and it never appears in `alembic.ini` or any committed file. The
Compose defaults above are local-only values, not secrets; see
`infrastructure/local/README.md`.

Migrations are portable, plain PostgreSQL. Nothing may depend on a
Supabase-specific feature (spec §8).

Tests that need a live database are marked `integration` and skip when
`TCG_API_DATABASE_URL` is unset, so the default suite never needs Docker:

```bash
uv run pytest -m integration   # requires PostgreSQL to be running
uv run pytest -m "not integration"
```
