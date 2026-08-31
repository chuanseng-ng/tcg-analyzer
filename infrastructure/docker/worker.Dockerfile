# syntax=docker/dockerfile:1
#
# services/api, run as the analysis worker — the Celery consumer of spec §65's
# job queue.
#
# Build from the REPOSITORY ROOT, for the reason `api.Dockerfile` gives:
# `services/api` is a member of the uv workspace and cannot be resolved without
# the root `pyproject.toml`, `uv.lock` and its sibling members.
#
#   docker build -f infrastructure/docker/worker.Dockerfile -t tcg-worker:dev .
#
# **Why this is not the API image with a different command, which is what it was
# until #36.** The image-quality gate brought OpenCV, and a CV stack — tens of
# megabytes of native decoders parsing untrusted input — has no business inside
# an internet-facing web server. The two images now differ by exactly one uv
# extra: `api.Dockerfile` syncs `--package tcg-api`, and this one adds
# `--extra worker`. Nothing else about them diverges, and nothing should: the
# point of running jobs from the application's own code is still that a worker
# cannot drift from the API that enqueued to it.
#
# What holds the split up at runtime is that `tcg_api.analysis.jobs` imports the
# gate's and the condition step's wiring inside `_advance` rather than at module
# scope. The API imports
# `jobs` merely to enqueue, so a module-level import would drag OpenCV into an
# image that does not have it and the container would fail to start.
# `services/api/tests/test_import_purity.py` asserts it.

# --------------------------------------------------------------------------
# Builder — resolve the workspace, plus the worker's extra, into a virtual
# environment.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY packages/ packages/
COPY services/ services/
COPY ml/ ml/

# `--extra worker` is the whole difference from the API image. `--frozen` fails
# rather than silently relocking, so this is built from exactly the resolution
# that was reviewed.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package tcg-api --extra worker

# No `alembic.ini` and no `database/`: a worker does not migrate. The one-shot
# `migrate` service runs from the API image, which carries both.

# --------------------------------------------------------------------------
# Runtime — the environment and the source, run unprivileged.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Uploaded card images are untrusted input and this is the container that
# *decodes* them (spec §56). Same uid as the API image, so a bind-mounted path
# has one owner across both.
RUN groupadd --system --gid 1001 tcg \
    && useradd --system --uid 1001 --gid tcg --create-home tcg

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder --chown=tcg:tcg /app /app

USER tcg

# No EXPOSE and no port. A worker takes its work from the broker; nothing
# should be able to reach it directly.

# `celery inspect ping` answers over the broker, so this proves the one
# dependency that matters — the worker is still connected to Redis. It lives
# here rather than in the Compose file because there is no HTTP server in this
# image to inherit the API's `/health` probe, which is what made the stack
# report unhealthy before the images were split.
#
# `$HOSTNAME` names *this* worker: without a destination the reply may come from
# any worker on the broker, which is not what a per-container healthcheck asks.
HEALTHCHECK --interval=15s --timeout=10s --start-period=20s --retries=5 \
    CMD celery --app tcg_api.analysis.worker inspect ping --destination "celery@$HOSTNAME"

CMD ["celery", "--app", "tcg_api.analysis.worker", "worker", \
     "--queues", "analysis", "--concurrency", "2"]
