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
uv run pytest           # repository-level tests
```

Build, lint and per-service commands are added as later M0 issues introduce
them.

#### API

```bash
uv run uvicorn tcg_api.main:app --reload   # API on http://localhost:8000
```

`GET /health` reports the service status and the application version; the
OpenAPI schema is at `/openapi.json` and the interactive documentation at
`/docs`. Settings are read from `TCG_API_`-prefixed environment variables.

The image is built from the repository root, because `services/api` is a member
of the uv workspace and cannot be resolved without it:

```bash
docker build -f infrastructure/docker/api.Dockerfile -t tcg-api:dev .
docker run --rm -p 8000:8000 tcg-api:dev
```

Compose wiring arrives with #20.
