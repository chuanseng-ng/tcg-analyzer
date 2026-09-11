# `infrastructure/deployment`

The production overlay: the file one host runs to serve the beta. It sits on
top of the local stack (`-f local -f prod`), which is where the services, images
and startup order come from. This overlay changes only what a host must not
inherit from a developer's machine. [ADR 0012](../../docs/adr/0012-the-beta-runs-from-a-compose-overlay.md)
records why, and what it leaves undecided.

| File | What |
| --- | --- |
| `docker-compose.prod.yml` | The overlay |
| `Caddyfile` | The edge: TLS, the web application at `/`, and the API's public routes under `/api` |
| `.env.example` | Every variable the overlay reads. Copy it to `.env` beside it, which is gitignored |

## What a host needs

- **Docker Engine 26 or later, with Compose v2.** From 26, containers that sit
  only on internal networks get no external DNS, and the worker's isolation
  relies on that.
- **A domain whose DNS points at the host,** with ports 80 and 443 (TCP and UDP)
  reachable. Caddy obtains the certificate itself on first start.
- **A filled `infrastructure/deployment/.env`.** Generate every secret with
  `openssl rand -hex 32`.

## Bring it up

Every command names the environment file. Without it, Compose reads
`infrastructure/local/.env` instead, which is a developer's file. Every
required variable is `:?`, so a missing one stops the stack rather than
falling back to the local file's obvious defaults.

```bash
cp infrastructure/deployment/.env.example infrastructure/deployment/.env
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml up -d --wait
```

The first `up` does five things in order:

1. Builds the images. The web image is built for `TCG_DOMAIN`.
2. Generates the broker's certificate.
3. Creates the application's MinIO account.
4. Runs the migrations.
5. Waits for every service to be healthy.

Running `up` again is safe: each of those one-off jobs is idempotent.

## Once, after the first `up`

Two operator commands, in this order. Neither is a service that runs on every
`up`:

```bash
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml exec api tcg-seed-grading-rules
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml run --rm catalog-import --version pokemon-catalog-tcgdex-v0.1.0
```

- **The grading rules come first.** The seed is idempotent.
- **The catalog import runs as a service of its own.** It is the one command
  that needs the internet (it reads `api.tcgdex.net`, [ADR 0004](../../docs/adr/0004-the-canonical-card-catalog-source.md)),
  and the API deliberately has no route out.
  - Its arguments are the command's own; `--help` lists them.
  - A published catalog version is immutable, so a later import takes a new
    version.

## What the overlay changes

These are the lines of #263's §56 checklist, and where each one landed:

| What | How |
| --- | --- |
| `/internal` is never public | The Caddyfile routes an allow-list under `/api`. `/internal/*`, `/docs` and `/openapi.json` are 404 |
| No egress for the application | Networks `backend` and `edge` are `internal: true`. Only `proxy` (and `catalog-import`, when run) is on `public` |
| No published datastore | `ports: !reset []` on every service except `proxy` |
| The worker's filesystem | `read_only: true`, with `/tmp` on a tmpfs (beat's schedule) |
| The worker's limits | `mem_limit: 2g` and `pids_limit: 512`, marked unmeasured until a real photograph is timed |
| Privileges | The local file's `cap_drop: ALL`, `no-new-privileges` and uid 1001 stay |
| Object storage | `minio-init` creates an account scoped to the one bucket. MinIO's root credentials are in no application container |
| The broker | TLS only (`rediss://`), verified against a private CA that `certs` generates on first `up` |
| Client addresses | Caddy overwrites `X-Forwarded-For`, and the API reads it with `TCG_API_TRUSTED_PROXY_COUNT=1` |
| Logs | JSON, the default |

**One worker, always.** Beat is embedded in it, so a second replica would be a
second scheduler.

**The annotation tool is off** (profile `annotation`). It is a browser client
of `/internal`, which this host never routes, so it stays a tool of the local
stack.

## Rotation, and what destroys data

- **`down -v` destroys the database, every photograph, the broker's
  certificate and Caddy's certificates.** Use `down`.
- **The broker's certificate.** After `down`, remove the `tcg-analyzer_redis-certs`
  volume. The next `up` generates a new one.
- **`POSTGRES_PASSWORD`** is applied only when the data volume is first
  initialised. Changing it later means an `ALTER ROLE` as well as the edit.
- **The MinIO application key.** Change it in `.env` and `up`. The old account
  remains until it is removed with `mc admin user rm` inside `minio`.

## Not decided here

These are all for the later ADR: the host or platform, DNS, backups (the state
is the `postgres-data` and `minio-data` volumes), monitoring beyond
`docker compose logs`, a secrets manager, HSTS, and an ingress for the
annotation tool.
