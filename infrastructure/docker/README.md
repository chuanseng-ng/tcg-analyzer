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
docker build -f infrastructure/docker/worker.Dockerfile -t tcg-worker:dev .
docker build -f infrastructure/docker/web.Dockerfile -t tcg-web:dev .
```

## What exists today

| File | Image | Shape |
| --- | --- | --- |
| `api.Dockerfile` | `services/api` — FastAPI, and the Alembic migrations | production |
| `worker.Dockerfile` | `services/api` run as the Celery analysis worker | production |
| `web.Dockerfile` | `apps/web` — Next.js | development |

`api.Dockerfile` carries `alembic.ini` and `database/` as well as the
application, so the local stack's one-shot `migrate` service runs the migrations
from the same image as the code that reads the schema.

`worker.Dockerfile` is the same application plus one uv extra, `worker`, which
brings `ml/image-quality` and with it OpenCV. **That extra is the whole reason
there are two images.** The worker decodes untrusted photographs; the API
answers HTTP; putting a CV stack in the container facing the internet buys
nothing and widens the attack surface (spec §56). The worker was the API image
with a different command until the quality gate arrived (#36), which is exactly
when the condition stopped holding.

What keeps the split real is a lazy import: `tcg_api.analysis.jobs` reaches the
gate from inside the function that runs a job, because the API imports that
module merely to enqueue. Move it to the top of the file and the API container
stops starting. `services/api/tests/test_import_purity.py` asserts it.

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

The ML image (M6). It needs NVIDIA Container Toolkit + CUDA for local GPU
development; the Compose file is shaped so that service is an addition rather
than a restructure.
