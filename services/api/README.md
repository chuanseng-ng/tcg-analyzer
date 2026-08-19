# `services/api`

The FastAPI HTTP surface — the only component the web application talks to.

Owns request validation, the error taxonomy, OpenAPI generation and the
reproducibility record attached to every analysis. It rejects model output whose
grade distribution is invalid (spec §63).

## What exists today

Bootstrapped in M0 (#13): the application factory, configuration, structured
logging and the health endpoint. M1 added the card catalog's PostgreSQL side
and the immutable version record spec §57's reproducibility field needs.
There are still no analysis endpoints and no authentication.

| Path | Responsibility |
| --- | --- |
| `src/tcg_api/app.py` | `create_app()` — assembles the FastAPI application |
| `src/tcg_api/main.py` | `app` — the ASGI entrypoint for `uvicorn` |
| `src/tcg_api/config.py` | `Settings`, read from `TCG_API_*` environment variables |
| `src/tcg_api/logging.py` | structlog configuration, uvicorn's loggers included |
| `src/tcg_api/version.py` | `application_version()` — the sole source of the version |
| `src/tcg_api/database.py` | the async engine, session factory and connectivity probe |
| `src/tcg_api/catalog/tables.py` | spec §10's `sets`, `cards`, `card_external_ids`, and `card_database_versions` |
| `src/tcg_api/catalog/seed.py` | `tcg-seed-catalog` — loads `database/seeds/catalog/` |
| `src/tcg_api/catalog/versions.py` | reads and publishes the catalog version |
| `src/tcg_api/routers/health.py` | `GET /health` |
| `src/tcg_api/routers/catalog.py` | `GET /catalog/version` |

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

`card_database_versions` joins the three §10 tables on the same `MetaData`. It
is provenance rather than catalog content — one immutable row per import run —
and it is declared alongside them because `env.py` compares the whole `MetaData`
against the database, so a table declared where autogenerate cannot see it is a
table the next generated revision proposes dropping.

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

### `GET /catalog/version`

```json
{
  "version": "pokemon-catalog-seed-v0.0.0",
  "source": "manual",
  "source_license": null,
  "source_revision": null,
  "generated_at": "2026-08-18T00:00:00Z",
  "record_counts": { "sets": 4, "cards": 22, "external_ids": 23 }
}
```

Which card catalog this deployment is serving — spec §57's
`card_database_version`, and the provenance ADR 0004 requires travel with every
import. **Deliberately not part of `GET /health`**, which reads the database for
nothing and must keep answering while PostgreSQL is down. Spec §64's endpoint
list is conceptual and names no catalog endpoint; this is an addition to it
rather than a deviation from it.

Two things can go wrong, and both answer `503` with the spec §66 envelope under
`provider_error`, distinguished by `details.reason`:

| `reason` | Meaning |
| --- | --- |
| `catalog_unreachable` | The database is unreachable, or none is configured |
| `no_catalog_version_registered` | The schema is migrated but nothing has been seeded or imported |

Neither is a `404`. The taxonomy has eight codes and no `not_found`, and adding
a ninth is a specification change — but a 404 would be wrong anyway: nothing is
missing that a different request would find. The deployment has no catalog.

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
