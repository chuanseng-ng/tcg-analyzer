# `infrastructure/docker`

Dockerfiles for the web app, API, analysis worker and ML images. The Compose
file that assembles them lives in [`../local`](../local) — this directory holds
images, not stacks.

Every image here is built from the **repository root**, because both workspaces
are only resolvable there: `services/api` is a uv workspace member needing the
root `pyproject.toml` and `uv.lock`, and `apps/web` is a pnpm workspace member
needing the root `pnpm-lock.yaml`.

```bash
docker build -f infrastructure/docker/api.Dockerfile -t tcg-api:dev .
docker build -f infrastructure/docker/web.Dockerfile -t tcg-web:dev .
```

## What exists today

| File | Image | Shape |
| --- | --- | --- |
| `api.Dockerfile` | `services/api` — FastAPI, and the Alembic migrations | production |
| `web.Dockerfile` | `apps/web` — Next.js | development |

`api.Dockerfile` carries `alembic.ini` and `database/` as well as the
application, so the local stack's one-shot `migrate` service runs the migrations
from the same image as the code that reads the schema.

`web.Dockerfile` is **development-shaped**: it runs `next dev`. Production
packaging for the web app is deliberately absent — it is a deployment concern,
and M0 (#20) explicitly excludes deployment configuration. See
[ADR 0003](../../docs/adr/0003-the-local-development-stack.md).

## Non-root is the baseline, not an optimisation

Both images create an unprivileged `tcg` user (uid/gid 1001) and drop to it, and
every service in the Compose file sets `no-new-privileges`.

This is established now, while the images do nothing interesting. Uploaded card
images are untrusted input, and spec §56 requires the ML worker to run with
minimal privileges, no unnecessary network access and restricted filesystem
access. Retrofitting that onto a worker written without it is far harder than
inheriting it.

## Still to come

The analysis worker (M2) and the ML image (M6). The ML image needs NVIDIA
Container Toolkit + CUDA for local GPU development; the Compose file is shaped
so that service is an addition rather than a restructure.
