# `infrastructure/local`

Local development environment: Docker Compose, PostgreSQL, MinIO and the
supporting services needed to run the complete application from a fresh clone.

## Current state

`docker-compose.yml` defines **PostgreSQL only** (`postgres:17-alpine`). The
`api`, `web` and `minio` services extend this same file in #20 — there will not
be a second Compose file.

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait
docker compose -f infrastructure/local/docker-compose.yml ps
docker compose -f infrastructure/local/docker-compose.yml logs -f postgres
docker compose -f infrastructure/local/docker-compose.yml down      # stop, keep data
docker compose -f infrastructure/local/docker-compose.yml down -v   # stop, discard data
```

`--wait` blocks until the `pg_isready` healthcheck passes, so a following
`alembic upgrade head` does not race the database's startup.

Data lives in the named volume `postgres-data`. `down -v` discards it; the next
`up` starts from an empty database, which is how the migration harness is
verified against a genuinely fresh volume.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_USER` | `tcg` | Database role |
| `POSTGRES_PASSWORD` | `tcg` | Role password |
| `POSTGRES_DB` | `tcg` | Database name |
| `POSTGRES_PORT` | `5432` | Published host port |

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
```

## Why plain upstream PostgreSQL

Spec §8 leaves the deployment platform open between ordinary PostgreSQL and
Supabase. Local development runs the lowest common denominator so that nothing
can quietly acquire a dependency on a feature only one of them provides.
