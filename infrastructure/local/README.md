# `infrastructure/local`

Local development environment: Docker Compose, PostgreSQL, MinIO and the
supporting services needed to run the complete application from a fresh clone.

## The whole application, in one command

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait
```

That is M0's acceptance criterion: a fresh clone reaches a running application
with no setup step. It starts PostgreSQL and MinIO, runs the migrations, then
starts the API and the web app in dependency order.

| Service | Address | What it is |
| --- | --- | --- |
| `web` | <http://localhost:3000> | Next.js application |
| `api` | <http://localhost:8000> | FastAPI service — `/health`, `/readiness`, `/docs` |
| `postgres` | `localhost:5432` | PostgreSQL 17 |
| `minio` | <http://localhost:9000> | S3 API — console on <http://localhost:9001> |
| `migrate` | — | one-shot `alembic upgrade head`, then exits |

The landing page reports whether it can reach the API, so "Analysis API
reachable" on <http://localhost:3000> is the end-to-end proof that the stack is
wired together.

```bash
docker compose -f infrastructure/local/docker-compose.yml ps -a
docker compose -f infrastructure/local/docker-compose.yml logs -f api
docker compose -f infrastructure/local/docker-compose.yml down      # stop, keep data
docker compose -f infrastructure/local/docker-compose.yml down -v   # stop, discard data
```

### Hot reload

`up` starts the stack; **`watch`** starts it and then syncs source changes into
the running containers:

```bash
docker compose -f infrastructure/local/docker-compose.yml watch
```

Editing `apps/web` or `services/api/src` is picked up without a rebuild — Next's
fast refresh and uvicorn's `--reload` do the rest. Changing `uv.lock` or
`pnpm-lock.yaml` rebuilds the affected image instead, because a new dependency
cannot be copied into an already-resolved environment.

Files are **synced into** the containers rather than bind-mounted. Bind-mounted
file watching from a Windows or macOS host is slow enough to need polling, and a
mount over `/app` would shadow the `node_modules` and `.venv` the image
installed. See [ADR 0003](../../docs/adr/0003-the-local-development-stack.md).

### Migrations

The `migrate` service runs `alembic upgrade head` from the API's own image, so
the migrations that run cannot drift from the code that reads the schema. `api`
waits for it with `service_completed_successfully`, so a failed migration stops
the API from starting rather than leaving it serving against a schema that was
never applied.

This is the one exception to the rule below. `--wait` waits for services to be
*running or healthy*, and a container that has finished its job is neither — the
trap that made MinIO create its own bucket rather than use the idiomatic
one-shot `mc mb` container. Compose exempts a service depended on with
`service_completed_successfully`, and CI asserts that exemption rather than
trusting it.

Every **other** service here is long-running by design.

### Data

Data lives in the named volumes `postgres-data` and `minio-data`. `down -v`
discards both; the next `up` starts from an empty database and an empty bucket,
which is how the migration harness is verified against a genuinely fresh volume.

MinIO's browser console is on <http://localhost:9001>, which is the quickest way
to see what an upload actually produced.

### Running only the backing services

The host-based workflows in the root `README.md` — `uv run uvicorn` and
`pnpm --filter @tcg/web dev` — remain valid and are faster for a focused test
loop. Start just what they need:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_USER` | `tcg` | Database role |
| `POSTGRES_PASSWORD` | `tcg` | Role password |
| `POSTGRES_DB` | `tcg` | Database name |
| `POSTGRES_PORT` | `5432` | Published host port |
| `MINIO_ROOT_USER` | `tcg` | Object-store access key |
| `MINIO_ROOT_PASSWORD` | `tcglocaldev` | Object-store secret key |
| `MINIO_PORT` | `9000` | Published S3 API port |
| `MINIO_CONSOLE_PORT` | `9001` | Published browser console port |
| `TCG_API_STORAGE_BUCKET` | `tcg-local` | Bucket MinIO creates on startup |
| `API_PORT` | `8000` | Published API port |
| `WEB_PORT` | `3000` | Published web port |
| `TCG_API_LOG_FORMAT` | `console` | `console` locally, `json` in a deployment |
| `TCG_API_LOG_LEVEL` | `INFO` | Root log level |

Override them with an untracked `.env` beside `docker-compose.yml` — most often
`POSTGRES_PORT=5433`, when something already owns 5432.

Only the **published** ports are configurable, and only they matter to a
developer. Inside the Compose network the services always reach each other on
the container port under the service name — `postgres:5432`, `minio:9000` — so
moving a published port cannot break service-to-service traffic. The API's CORS
origin is derived from `WEB_PORT` and the web app's API base URL from
`API_PORT`, so moving either keeps the pair consistent.

**The defaults are not secrets.** They are deliberately obvious local-only
values so that a fresh clone runs without a setup step and so nobody mistakes
them for something worth protecting. Production credentials come from the
environment and secrets handling introduced in #18, never from a committed
default.

Point the application and Alembic at the result with:

```bash
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
```

Copying `.env.example` to `.env` sets all of them at once, including the
matching `TCG_API_STORAGE_*` credentials.

## Why MinIO

Spec §8 asks for "S3-compatible object storage" and names no provider. MinIO
speaks the S3 API, so local development exercises the same protocol and the same
signature algorithm a deployment will, against a container rather than a billing
account. Nothing in the application knows which of the two it is talking to —
that is what the port in `packages/shared` is for (ADR 0002).

## Why plain upstream PostgreSQL

Spec §8 leaves the deployment platform open between ordinary PostgreSQL and
Supabase. Local development runs the lowest common denominator so that nothing
can quietly acquire a dependency on a feature only one of them provides.
