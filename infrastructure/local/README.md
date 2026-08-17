# `infrastructure/local`

Local development environment: Docker Compose, PostgreSQL, MinIO and the
supporting services needed to run the complete application from a fresh clone.

## Current state

`docker-compose.yml` defines **PostgreSQL** (`postgres:17-alpine`) and
**MinIO**, which creates its bucket on startup so a fresh volume comes up
usable. The `api` and `web` services extend this same file in #20 — there will
not be a second Compose file.

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait
docker compose -f infrastructure/local/docker-compose.yml ps
docker compose -f infrastructure/local/docker-compose.yml logs -f postgres
docker compose -f infrastructure/local/docker-compose.yml down      # stop, keep data
docker compose -f infrastructure/local/docker-compose.yml down -v   # stop, discard data
```

`--wait` blocks until every healthcheck passes, so a following
`alembic upgrade head` does not race the database's startup and the first signed
request does not race MinIO's.

Every service here is long-running by design. `--wait` waits for services to be
*running or healthy*, so a one-shot container — the idiomatic `mc mb` way to
create a bucket — would make this command report failure even after exiting 0.

Data lives in the named volumes `postgres-data` and `minio-data`. `down -v`
discards both; the next `up` starts from an empty database and an empty bucket,
which is how the migration harness is verified against a genuinely fresh volume.

MinIO's browser console is on <http://localhost:9001>, which is the quickest way
to see what an upload actually produced.

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

Override them with an untracked `.env` beside `docker-compose.yml` — most often
`POSTGRES_PORT=5433`, when something already owns 5432.

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
