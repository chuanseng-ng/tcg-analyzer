# `services/api`

The FastAPI HTTP surface — the only component the web application talks to.

Owns request validation, the error taxonomy, OpenAPI generation and the
reproducibility record attached to every analysis. It rejects model output whose
grade distribution is invalid (spec §63).

## What exists today

Bootstrapped in M0 (#13): the application factory, configuration, structured
logging and the health endpoint. M1 added the card catalog's PostgreSQL side.
There are still no domain endpoints and no authentication.

| Path | Responsibility |
| --- | --- |
| `src/tcg_api/app.py` | `create_app()` — assembles the FastAPI application |
| `src/tcg_api/main.py` | `app` — the ASGI entrypoint for `uvicorn` |
| `src/tcg_api/config.py` | `Settings`, read from `TCG_API_*` environment variables |
| `src/tcg_api/logging.py` | structlog configuration, uvicorn's loggers included |
| `src/tcg_api/version.py` | `application_version()` — the sole source of the version |
| `src/tcg_api/database.py` | the async engine, session factory and connectivity probe |
| `src/tcg_api/catalog/tables.py` | spec §10's `sets`, `cards`, `card_external_ids` |
| `src/tcg_api/catalog/seed.py` | `tcg-seed-catalog` — loads `database/seeds/catalog/` |
| `src/tcg_api/routers/health.py` | `GET /health` |

### The card catalog

`catalog/tables.py` is the schema's source of truth: `database/migrations/env.py`
reads its `MetaData` as `target_metadata`, so a table declared only in a
migration would be proposed for deletion by `alembic revision --autogenerate`.
It is SQLAlchemy Core rather than ORM — `tcg_domain.catalog` already models a
card, and the adapter reads rows and constructs those entities rather than
mapping them.

It lives here rather than in `packages/domain` because the domain package
imports nothing but the standard library, which is what lets every ML module
import it.

### `GET /health`

```json
{ "status": "ok", "application_version": "0.0.0" }
```

Consults no database, network or filesystem, so it is usable as a container
readiness probe and answers even when a dependency is down. Dependency checks
belong to a separate readiness endpoint (#15).

`application_version` is read from the installed `tcg-api` distribution
metadata, which is the single source of truth spec §57 requires — every
analysis records the version that produced it, so it must not be duplicated.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TCG_API_LOG_LEVEL` | `INFO` | Root log level |
| `TCG_API_LOG_FORMAT` | `json` | `json` or `console` |
| `TCG_API_CORS_ORIGINS` | `["http://localhost:3000"]` | Origins permitted to call the API |

### Running

```bash
uv run uvicorn tcg_api.main:app --reload   # http://localhost:8000
uv run pytest services/api                 # tests
uv run tcg-seed-catalog                    # load the catalog fixtures
```

The container image is `infrastructure/docker/api.Dockerfile`, built from the
repository root — see the root `README.md`.
