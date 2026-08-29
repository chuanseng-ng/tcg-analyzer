# syntax=docker/dockerfile:1
#
# apps/annotation — the internal annotation tool, for local development.
#
# A near-copy of web.Dockerfile, and deliberately so: the two applications share
# a stack (ADR 0001), so they share a build. Two files rather than one
# parametrized by an argument, because a Dockerfile that takes the app name as a
# variable is harder to read than the ten lines it saves — and because these two
# are free to diverge when the annotation tool's deployment does.
#
# Build from the REPOSITORY ROOT, not from this directory: `apps/annotation` is a
# member of the pnpm workspace and the lockfile lives at the root.
#
#   docker build -f infrastructure/docker/annotation.Dockerfile -t tcg-annotation:dev .
#
# This image is DEVELOPMENT-SHAPED: it runs `next dev`, and it is what
# `infrastructure/local/docker-compose.yml` starts. There is deliberately no
# production stage — packaging for deployment is out of scope for M0 (#20), and
# a standalone build would require moving `outputFileTracingRoot` in
# `next.config.mjs` back to the repository root, undoing a fix whose reason is
# recorded in that file. See docs/adr/0003-the-local-development-stack.md.

FROM node:26-bookworm-slim AS development

# Corepack provisions the exact pnpm pinned by the root `package.json`'s
# `packageManager` field, hash and all, so the image resolves dependencies with
# the same pnpm that CI and developers use. The prompt would otherwise block a
# non-interactive build when it downloads that version.
#
# Node no longer ships Corepack in the distribution, so it is installed from npm
# rather than assumed present — without this, `corepack enable` exits 127 on
# node:26. Do not swap it for `npm install -g pnpm@<version>`: that duplicates
# the version out of `package.json` and drops its integrity hash, which is the
# whole reason Corepack is here.
ENV PNPM_HOME=/pnpm \
    PATH="/pnpm:$PATH" \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NEXT_TELEMETRY_DISABLED=1
RUN npm install --global corepack && corepack enable

# Uploaded card images are untrusted input, and nothing here needs root. The
# same uid/gid as the API image, so the two are consistent when a future
# service shares a volume with either.
RUN groupadd --system --gid 1001 tcg \
    && useradd --system --uid 1001 --gid tcg --create-home tcg

WORKDIR /app

# Manifests first: an edit to a component must not reinstall the dependency
# tree. `pnpm-workspace.yaml` is required for `apps/annotation` to resolve as a
# workspace member at all, and it carries the `allowBuilds` entries without
# which the ESLint resolver has no native binary.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/annotation/package.json apps/annotation/

# `--frozen-lockfile` fails rather than silently relocking, so the image is
# built from exactly the resolution that was reviewed and tested.
RUN --mount=type=cache,target=/pnpm/store \
    pnpm install --frozen-lockfile

COPY apps/annotation/ apps/annotation/

# `next dev` writes `.next/` and Compose watch syncs source into this tree, so
# both must be writable by the unprivileged user. Done as one step after the
# copies rather than with `COPY --chown`, because the installed tree above is
# root-owned too.
RUN chown -R tcg:tcg /app

USER tcg

WORKDIR /app/apps/annotation

EXPOSE 3001

# `next dev` compiles a route on first request, so the first probe pays for a
# build the later ones do not — hence the long start period and the timeout
# that is generous by the standards of the API's.
HEALTHCHECK --interval=15s --timeout=10s --start-period=45s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3001/').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

# `--hostname 0.0.0.0` because the default binds the loopback interface only,
# which no other container and no browser on the host could reach.
CMD ["pnpm", "exec", "next", "dev", "--hostname", "0.0.0.0", "--port", "3001"]
