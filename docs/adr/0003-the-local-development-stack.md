# ADR 0003 — The local development stack

- **Status:** accepted
- **Date:** 2026-08-18
- **Refs:** M0, #20, spec §8, §56, §69/M0

## Context

M0's acceptance criterion is a single sentence: *a developer can clone the
repository and run the complete empty application locally*. Every part existed
before this change — FastAPI, Next.js, PostgreSQL, MinIO, environment
configuration — and none of them were joined up. Running the application meant
starting Compose for the backing services, then `uvicorn` in one terminal and
`pnpm dev` in another, exporting variables by hand between them.

Issue #20 also carries an instruction that outlives it: establish the non-root,
least-privilege container baseline **now**, while there is nothing to break.
Spec §56 requires the ML worker to run with minimal privileges, restricted
filesystem access and no unnecessary network access. That worker arrives in M6,
and a baseline it inherits costs nothing, where one retrofitted onto it later is
a rewrite.

Four questions had no obvious answer.

**Where the Compose file lives.** Issue #20's scope names
`infrastructure/docker`. That was written before #15 and #17 landed, and both
put the file in `infrastructure/local` instead.

**How hot reload works.** The issue asks for it. The obvious mechanism — bind
mount the source and run the framework's dev server — has two problems here: a
mount over `/app` shadows the `node_modules` and `.venv` the image installed,
and bind-mounted file watching from a Windows or macOS host is slow enough that
it usually needs polling, which burns CPU indefinitely.

**What shape the web image is.** `api.Dockerfile` is production-shaped. The
symmetric choice would be a production web image, but the issue's non-goals
exclude production deployment configuration, and `apps/web/next.config.mjs`
pins `outputFileTracingRoot` to `apps/web` for a documented reason — a
standalone build would require moving it back to the repository root, past the
second (Python) workspace it was pinned to avoid.

**When migrations run.** Nothing ran them automatically, and the API image did
not contain `alembic.ini` or `database/`, so nothing could.

## Decision

```text
infrastructure/local/docker-compose.yml   postgres, minio, migrate, api, web
infrastructure/docker/api.Dockerfile      production-shaped; also runs alembic
infrastructure/docker/web.Dockerfile      development-shaped; runs next dev
```

**One Compose file, in `infrastructure/local`.** `infrastructure/docker` holds
images; `infrastructure/local` holds the stack that assembles them. The path
`infrastructure/local/docker-compose.yml` is already named by the CI storage
job, by ADR 0002, by the root README, and — the reason that settles it — by two
runtime error messages that tell a developer what to run, in
`services/api/src/tcg_api/storage.py` and `database/migrations/env.py`. Moving
the file would churn eight files and two tested error strings to satisfy a
sentence written before the decision existed. Issue #20's scope line was
corrected to match, exactly as #12's was by ADR 0001.

**Hot reload is a file sync, not a bind mount.** Compose's `develop.watch`
copies changed files into the running container. There is no mount to be slow,
and nothing shadows the installed dependencies. `uv` installs workspace members
editable, so a synced Python file is picked up with no reinstall; `next dev`
recompiles on its own. A change to `uv.lock` or `pnpm-lock.yaml` triggers
`action: rebuild` instead, because a new dependency cannot be copied into an
already-resolved environment.

This makes two commands, and both are documented:

- `docker compose … up` starts the stack — **this is the acceptance criterion**;
- `docker compose … watch` starts it and live-syncs source.

**The web image is development-shaped, and says so.** It runs `next dev`. There
is no production stage, because production packaging is an explicit non-goal of
#20 and building one would mean undoing a deliberate fix in `next.config.mjs`.
CI builds the image on every PR so it cannot rot.

**Migrations are a separate one-shot service.** `migrate` runs `alembic upgrade
head` from the API's own image, and `api` waits on it with
`service_completed_successfully`. `api.Dockerfile` gained `alembic.ini` and
`database/` — copied after the dependency sync, so editing a migration does not
reinvalidate resolution.

**Non-root, and honest about its edges.** The two images this repository builds
run as uid 1001, and every service sets `no-new-privileges`.

## Consequences

- One command produces a running application, and CI asserts it: the `compose`
  job starts the stack and requires `/readiness` to return 200 with both
  `database` and `storage` reporting `ok`. `/readiness` rather than `/health`
  is the assertion that matters — `/health` is dependency-free by design and
  would pass even if nothing were wired together.
- **The migration service is the one short-lived container in a file whose
  header rule is that everything is long-running.** `up --wait` waits for
  services to be running or healthy, and a container that has finished its job
  is neither; that is why MinIO creates its own bucket rather than use a
  one-shot `mc mb` container. Compose exempts a service depended on with
  `service_completed_successfully`, so the rule now has a stated exception
  rather than a silent one. CI asserts the exemption holds. Were it ever to
  stop holding, the fallback is `profiles: ["migrate"]` and an explicit
  `docker compose --profile migrate run --rm migrate`.
- **The non-root claim covers the images this repository builds, and no more.**
  `api` and `web` run as uid 1001. The PostgreSQL image drops to its own
  `postgres` user internally. The MinIO image runs as root, and pinning `user:`
  would fight its named volume's ownership on first start; it is left alone and
  recorded here rather than quietly counted as covered. `tests/test_compose_stack.py`
  encodes exactly this split, so the distinction survives review.
- `.dockerignore` had to be corrected: a bare `node_modules` matches only the
  context root, so `apps/web/node_modules` — the one that actually exists, full
  of binaries compiled for the host — was being sent to the daemon on every
  build. The same was true of `.next` and the nested Python caches.
- **`NEXT_PUBLIC_API_BASE_URL` points at `localhost`, not at `api`.**
  `ApiStatus` is a client component, so the only fetch in the application runs
  in the browser, where `http://api:8000` resolves for nothing. A future
  server-side fetch will need a second, network-internal variable; it would be
  speculative to add one now. The same asymmetry already applies to storage:
  ADR 0002 records that a URL signed against `http://minio:9000` is unreachable
  from a host browser, and defers the public-endpoint setting to M2, when there
  is finally something to upload.
- The stack runs development servers, so it is not evidence that a production
  image works. The API's production image is built in CI and is what
  `migrate` and `api` actually run; the web app's production packaging remains
  genuinely unbuilt, and M6's deployment work will have to write it.
- Adding the M6 GPU service is an addition rather than a restructure: it joins
  the same file, inherits the same hardening anchor, and gains the extra
  isolation §56 requires on top.
