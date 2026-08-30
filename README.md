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
[`docs/adr/`](docs/adr), and what each image-processing stage actually does is in
[`docs/analysis-pipeline.md`](docs/analysis-pipeline.md).

### The reproducibility record

Spec §57 requires every analysis to record what it was computed against, so a
historical answer can be re-derived rather than re-guessed. Eight fields, and
`GET /analyses/{id}` reports them together under `reproducibility`:

| field | in V1 |
| --- | --- |
| `analysis_id` | the analysis's own `id` |
| `application_version` | the version of the service that ran it |
| `card_database_version` | the published catalog identifier that was current |
| `image_sha256` | a digest per side, of the bytes that were *stored* |
| `model_bundle_version` | null — no model exists yet |
| `grading_rules_version` | null — the rules exist, but no run applies one |
| `market_snapshot_id` | null — nothing has ingested, so there is none to name |
| `economic_configuration_id` | null — the economic engine is a later milestone |

The values are captured when the run claims the analysis — never resolved when
it is read — and a written field cannot be changed afterwards, because a trigger
refuses it. A null is a documented absence rather than an omission, and the two
kinds differ: the model bundle, the economic configuration and an ingested price
**do not exist yet**, while grading rules exist and are simply not consulted by
any analysis until per-company grade prediction arrives. Recording a rules
version on a run that never applied one would be a false claim rather than a
record.

[`docs/architecture.md`](docs/architecture.md) carries the reasoning, along with
the `grading_rules`, `market_observations` and `market_snapshots` schemas that
back three of these fields.

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
  provisioned by Corepack, which Node no longer bundles: install it once with
  `npm install --global corepack`, then `corepack enable pnpm`. Prefix commands
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

That is the entire setup from a fresh clone. It starts PostgreSQL, MinIO and
Redis, runs the migrations, then starts the API, the analysis worker, the web
application and the internal annotation tool in dependency order.

| | |
| --- | --- |
| Web application | <http://localhost:3000> |
| Annotation tool | <http://localhost:3001> — internal, never a public surface |
| API | <http://localhost:8000> — `/health`, `/readiness`, `/docs` |
| MinIO console | <http://localhost:9001> |

The landing page reports whether it can reach the API, so **"Analysis API
reachable"** on <http://localhost:3000> means the whole stack is talking to
itself.

Swap `up` for `watch` to get hot reload — source changes are synced into the
running containers. Stop with `down`, or `down -v` to discard the database and
the bucket as well. See [`infrastructure/local`](infrastructure/local) for the
full reference.

The host-based workflows are the faster loop for a focused change, and they are
what CI runs. [`docs/development.md`](docs/development.md) has them in full.

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

### Checks

CI runs exactly these, so a green run locally means a green run there. Nothing
in the pipeline is a step you cannot reproduce yourself.

```bash
uv run ruff check .                     # lint
uv run ruff format --check .            # formatting
uv run mypy packages/domain/src packages/shared/src packages/grading-companies/src packages/market-data/src packages/economic-engine/src services/api/src ml/image-quality/src ml/card-detection/src ml/normalization/src ml/centering/src ml/corners/src ml/edges/src
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
| [`docs/analysis-pipeline.md`](docs/analysis-pipeline.md) | The quality gate, card detection, perspective normalization |
| [`docs/api.md`](docs/api.md) | The HTTP endpoints, what each one refuses, and why |
| [`docs/database.md`](docs/database.md) | Migrations, seeds, the catalog import, the test markers |
| [`docs/development.md`](docs/development.md) | Host-based workflows — web, object storage, background jobs |
| [`docs/retention.md`](docs/retention.md) | How long uploaded photographs are kept, and what deletes them |
| [`docs/market-provider-research.md`](docs/market-provider-research.md) | The rubric, the survey, and the licensing determinations |
| [`docs/adr/`](docs/adr) | Why things are the way they are, one decision per file |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Commits, pull requests, Definition of Done |
| [`.env.example`](.env.example) | Every variable the stack reads |
| `/docs` on the running API | The generated OpenAPI reference |

Each directory also carries its own `README.md` describing what belongs in it.
