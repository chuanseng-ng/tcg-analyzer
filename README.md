# tcg-analyzer

TCG Grading Advisor — photograph an ungraded Pokémon card, front and back, and
get back its identity, its physical condition, a probability distribution over
grades for each grading company, market values, the economics of grading it, and
a recommendation on whether grading is worthwhile.

## What this is, and what it is not

**This is not an official grading service.** It does not authenticate cards, and
it does not detect counterfeits. Its output is a probability distribution over
grades — never a guaranteed grade, and never a promise about what a grading
company will decide.

What it does is answer a question the grading companies do not: *given this
card, in this condition, at today's prices, is paying to have it graded worth
it?* Two independent halves produce that answer. The models predict the physical
outcome; the economic engine decides whether that outcome is worth pursuing.
Neither depends on the other's internals, and that separation is the single most
load-bearing rule in the codebase.

## V1 scope

| | |
| --- | --- |
| **Cards** | Pokémon, English and Japanese |
| **Grading companies** | PSA, TAG, BGS |
| **Input** | Ordinary front and back photographs, from a mobile camera or a desktop |
| **Analysis** | Identification, centering, corners, edges, surface, manufacturing defects, condition confidence |
| **Output** | A grade probability distribution per company, market values, grading economics, a recommendation |
| **Economics** | SGD, with configurable grading, shipping, insurance, miscellaneous and selling costs |
| **Sessions** | Anonymous. Mobile-first, desktop-compatible |

Deliberately **not** in V1: authentication, user accounts, collections,
portfolio management, social features, counterfeit detection, slab analysis,
crack-and-resubmit analysis, guided photography, grading submission, card
selling, CGC, ARS, other TCGs, currencies other than SGD, and monetization.

They are excluded from the scope, not from the design: the architecture has to
accommodate every one of them later without a rewrite.

## Architecture

Photographs become a neutral condition representation, that representation feeds
a separate model per grading company, and the resulting distributions meet a
pre-ingested market snapshot in the economic engine. Uncertainty is a valid
answer at every step — `insufficient_information` is a legitimate result, and
the pipeline stops rather than guessing when an image or an identification
cannot support the next stage.

The invariants that shape all of this — distributions rather than point grades,
replaceable providers, immutable versioning, provenance-gated training data —
are documented in **[`docs/architecture.md`](docs/architecture.md)**. Read it
before making a structural change; the decisions behind it are recorded in
[`docs/adr/`](docs/adr).

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
- **Docker**, with Compose v2.22+ — only for running the stack, not for the
  tests. `docker compose version` reports both.

### Run the whole thing

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait
```

That is the entire setup from a fresh clone. It starts PostgreSQL and MinIO,
runs the migrations, then starts the API and the web application in dependency
order.

| | |
| --- | --- |
| Web application | <http://localhost:3000> |
| API | <http://localhost:8000> — `/health`, `/readiness`, `/docs` |
| MinIO console | <http://localhost:9001> |

The landing page reports whether it can reach the API, so **"Analysis API
reachable"** on <http://localhost:3000> means the whole stack is talking to
itself.

Swap `up` for `watch` to get hot reload — source changes are synced into the
running containers. Stop with `down`, or `down -v` to discard the database and
the bucket as well. See [`infrastructure/local`](infrastructure/local) for the
full reference.

The sections below are the host-based workflows. They remain the faster loop
for a focused change, and they are what CI runs.

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
uv run mypy packages/domain/src packages/shared/src services/api/src
uv run pytest -m "not integration and not object_storage"   # both need Docker

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

The same image is what the local stack runs, both as the `api` service and as
the one-shot `migrate` service that applies the migrations before the API is
allowed to start.

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
Supabase-specific feature (spec §8), and no extension is required.

The schema's source of truth is `services/api/src/tcg_api/catalog/tables.py`,
which `database/migrations/env.py` reads as `target_metadata`. Declare a new
table there as well as in its migration, or `alembic revision --autogenerate`
will propose dropping it.

Once the schema is up, load the hand-authored card catalog fixtures:

```bash
uv run tcg-seed-catalog
```

Roughly twenty English and Japanese cards under a `manual` provider, enough to
search, identify and price against. It is idempotent, so re-run it after editing
a fixture; see `database/seeds/README.md`.

Tests that need a live database are marked `integration` and skip when
`TCG_API_DATABASE_URL` is unset, so the default suite never needs Docker:

```bash
uv run pytest -m integration   # requires PostgreSQL to be running
uv run pytest -m "not integration"
```

#### Object storage

Uploaded card images live in S3-compatible object storage — MinIO locally, any
S3-compatible provider in a deployment. The application never talks to either
directly: it goes through the `ObjectStorage` port in `packages/shared`, so the
provider is replaceable ([ADR 0002](docs/adr/0002-object-storage-behind-a-port.md)).

The same `up` that starts PostgreSQL starts MinIO and creates the bucket:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait

export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
```

The browser console is on <http://localhost:9001>. `down -v` discards the
`minio-data` volume along with the database's, so the next `up` starts from an
empty bucket.

Two rules matter more than the configuration:

- **Storage keys are generated server-side, always.** `generate_key` takes no
  filename argument, so a client-supplied name cannot reach a storage path
  (spec §55). An original filename may be kept as metadata via
  `sanitise_filename`, and never as a path.
- **Signed URLs are short-lived and scoped to one object.** A signed URL is a
  bearer credential nobody can revoke, so the only bound on its misuse is
  `TCG_API_STORAGE_SIGNED_URL_TTL_SECONDS`.

Tests that need a live MinIO are marked `object_storage` and skip when
`TCG_API_STORAGE_ENDPOINT_URL` is unset. They are separate from `integration`
because the two need different services:

```bash
uv run pytest -m object_storage   # requires MinIO to be running
```

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the working conventions: one primary
capability per pull request, Conventional Commits, the PR description headings,
and the Definition of Done a change has to meet before it is finished.

Two of those rules are worth repeating here, because their cost is paid in the
history rather than in review: **never commit model weights, training images,
API keys or provider credentials**, and **do not skip hooks**. The project may
be open-sourced later, so proprietary assets have to stay out of the history,
not merely out of the working tree.

## Documentation

| | |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | Domain architecture, the analysis pipeline, the invariants |
| [`docs/adr/`](docs/adr) | Why things are the way they are, one decision per file |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Commits, pull requests, Definition of Done |
| [`.env.example`](.env.example) | Every variable the stack reads |
| `/docs` on the running API | The generated OpenAPI reference |

Each directory also carries its own `README.md` describing what belongs in it.
