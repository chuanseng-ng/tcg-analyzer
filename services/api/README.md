# `services/api`

The FastAPI HTTP surface — the only component the web application talks to.

Owns request validation, the error taxonomy, OpenAPI generation and the
reproducibility record attached to every analysis. It rejects model output whose
grade distribution is invalid (spec §63).

## What exists today

Bootstrapped in M0 (#13): the application factory, configuration, structured
logging and the health endpoint. No domain endpoints, no authentication and no
database access yet.

| Path | Responsibility |
| --- | --- |
| `src/tcg_api/app.py` | `create_app()` — assembles the FastAPI application |
| `src/tcg_api/main.py` | `app` — the ASGI entrypoint for `uvicorn` |
| `src/tcg_api/config.py` | `Settings`, read from `TCG_API_*` environment variables |
| `src/tcg_api/logging.py` | structlog configuration, uvicorn's loggers included |
| `src/tcg_api/version.py` | `application_version()` — the sole source of the version |
| `src/tcg_api/routers/health.py` | `GET /health` |

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
```

The container image is `infrastructure/docker/api.Dockerfile`, built from the
repository root — see the root `README.md`.
