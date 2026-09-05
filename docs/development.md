# Development

The host-based workflows. They are the faster loop for a focused change, and
they are what CI runs — the Compose stack in the
[README](../README.md#run-the-whole-thing) is the one-command alternative.

Start only what a workflow needs:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
```

## The web application

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

## The annotation tool

```bash
pnpm install
pnpm --filter @tcg/annotation dev     # http://localhost:3001
pnpm --filter @tcg/annotation test
pnpm --filter @tcg/annotation build
```

Spec §30's internal tool. Same stack as the web application, same lint config
and test runner — one toolchain, deliberately. It reads a running `services/api`
at `NEXT_PUBLIC_API_BASE_URL`, and Next reads env files from the app directory,
so copy `apps/annotation/.env.example` to `apps/annotation/.env.local` for a
bare `pnpm dev`.

There is nothing to look at until a corpus exists. Ingest a card and produce the
artifact the tool shows:

```bash
uv run tcg-ingest-training-images --front front.jpg --back back.jpg --source first_party --acquisition-method photographed_before_submission --license "owned outright" --commercial-use-allowed --derivative-use-allowed --acquired-at 2026-08-01T10:00:00+08:00
uv run tcg-normalize-training-images
```

**It is not a public surface.** The tool reads `/internal/annotation`, and
[ADR 0009](adr/0009-the-dataset-store-as-a-database-domain.md) makes its
isolation a deployment question: the `/internal` prefix is what an ingress rule
matches, and the tool is served from an origin the public one does not route to.
Running it on :3001 beside the web application locally is a convenience, not a
statement about where it belongs — and there is no authentication, because V1
excludes accounts and an internal tool on a private network is the shape this
repository already assumes.

## Object storage

Uploaded card images live in S3-compatible object storage — MinIO locally, any
S3-compatible provider in a deployment. The application never talks to either
directly: it goes through the `ObjectStorage` port in `packages/shared`, so the
provider is replaceable ([ADR 0002](adr/0002-object-storage-behind-a-port.md)).

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

One module needs both. `services/api/tests/test_anonymous_journey.py` drives an
anonymous analysis through every endpoint — real photographs into the store,
the real worker reading them back, the results at the end — and carries both
markers, so it runs only where PostgreSQL and MinIO are both reachable:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
uv run alembic upgrade head
uv run pytest -m "integration and object_storage"
```

It seeds only idempotent rows and deletes its own sessions and objects
afterwards, so it is safe to point at a developer's `tcg` database. It refuses
`tcg_corpus`, as every integration test does.

## Background jobs

Analysis is asynchronous: `POST /analyses/{id}/run` hands the work to a Celery
worker over Redis and answers `queued` at once, because inference takes far
longer than an HTTP request should (spec §8). Progress is polled through
`GET /analyses/{id}`, which reports one of spec §65's nine states — `queued` is
an acknowledgement rather than a state, and no analysis ever holds it.

The same `up` starts Redis and the worker. The worker runs the same application
with a different command, from an image of its own — the API's plus the `worker`
extra, which brings the image-quality gate, the card detector and perspective
normalization, and with them OpenCV:

```bash
docker compose -f infrastructure/local/docker-compose.yml logs -f worker
```

Five properties are load-bearing rather than incidental, and each has a test:

- **The API image does not contain OpenCV.** The worker decodes untrusted
  photographs; the API answers HTTP. `tcg_api.analysis.jobs` imports the
  pipeline's wiring inside the function that runs a job rather than at module
  scope, because the API imports that module to enqueue — moving it to the top
  of the file is a tidy-up that stops the API container from starting.

- **The worker accepts JSON and nothing else.** A Celery worker willing to
  deserialize pickle from a broker an attacker can write to is arbitrary code
  execution. `task_serializer`, `result_serializer` and `accept_content` are all
  pinned, and none of them may ever gain `pickle`.
- **The broker is authenticated, even locally.** There is no default
  `TCG_API_REDIS_URL`; a deployment sets `rediss://` with credentials.
- **A repeat delivery is a no-op.** Delivery is at-least-once, and a run claims
  its analysis with a conditional `UPDATE` whose `WHERE` clause names the states
  the move is legal from — so a duplicate job finds nothing to do, and two
  workers cannot both claim one analysis.
- **A dead-lettered job records the job id, the error and the attempt count —
  and nothing else.** Analysis payloads reference photographs of somebody's card
  and their surroundings; keeping one indefinitely so a job nobody re-drives
  could be re-driven is not a trade this project makes (spec §54).

The worker also runs **Celery beat embedded**, which is what schedules spec §54's
retention sweep: hourly, `tcg_api.analysis.retention` deletes everything
belonging to a session that has expired — the stored objects first and the rows
second, so an interrupted sweep leaves a row naming an object that is gone rather
than an object nothing names. The schedule lives in the application rather than
in Compose, so `--beat` only decides which process runs it. Run one on demand:

```bash
celery --app tcg_api.analysis.worker call tcg_api.analysis.purge_expired
```

[`retention.md`](retention.md) is the policy, and it records the gaps
that remain open as gaps rather than pretending they are closed.
