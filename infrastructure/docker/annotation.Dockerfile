# syntax=docker/dockerfile:1
#
# apps/annotation — the internal annotation tool.
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
#   docker build --target production -f infrastructure/docker/annotation.Dockerfile -t tcg-annotation:prod .
#
# Two shapes, and `development` is the LAST stage on purpose, so the command
# above with no `--target` and the Compose service that names none both still
# get the development image:
#
#   development   runs `next dev`; what infrastructure/local/docker-compose.yml
#                 starts, and what ADR 0003's file sync syncs into
#   production    runs the built `.next/standalone` server; ADR 0009 keeps this
#                 one off the public origin, so the deployment overlay runs it
#                 on the internal network only
#
# Reordering the two silently changes what an untargeted build produces —
# `tests/test_compose_stack.py` asserts the ordering. See
# docs/adr/0003-the-local-development-stack.md and its 2026-09-07 addendum.

FROM node:26-bookworm-slim@sha256:cd9f682fa2885cd1056e830424764158570061c59736a1da836bc3d73df095ae AS base

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
# service shares a volume with either. Declared once here, so both shapes drop
# to the same user.
RUN groupadd --system --gid 1001 tcg \
    && useradd --system --uid 1001 --gid tcg --create-home tcg

WORKDIR /app


FROM base AS dependencies

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


FROM dependencies AS build

COPY apps/annotation/ apps/annotation/

# `NEXT_PUBLIC_*` values are INLINED INTO THE BROWSER BUNDLE at build time
# (`apps/annotation/lib/env.ts` says so), so this is a build argument and not a
# runtime setting: a production image is built per deployment, and the value
# can never be a secret. Deliberately without a default — unset leaves
# `lib/env.ts`'s own fallback as the single place that decides one.
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

# Asks `next.config.mjs` for `output: "standalone"`; see the reason it is
# behind a variable there.
ENV NEXT_OUTPUT_STANDALONE=1

RUN pnpm --filter @tcg/annotation build


FROM base AS production

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3001

# The standalone tree mirrors repository-root-relative paths, because
# `outputFileTracingRoot` is the repository root — so this unpacks onto `/app`
# and the server lands at /app/apps/annotation/server.js beside /app/node_modules.
# `.next/static` and `public/` are excluded from that tree on the assumption a
# CDN serves them; copied in, `server.js` serves them itself. There is no
# `public/` in this application to copy.
COPY --from=build --chown=tcg:tcg /app/apps/annotation/.next/standalone ./
COPY --from=build --chown=tcg:tcg /app/apps/annotation/.next/static ./apps/annotation/.next/static

USER tcg

EXPOSE 3001

# The same probe as the development stage's, on a short start period: the app
# is already built, so the first request pays for no compilation.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3001/').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

# `HOSTNAME=0.0.0.0` above is what the standalone server binds; the default
# binds the loopback interface only, which no other container and no browser on
# the host could reach.
CMD ["node", "apps/annotation/server.js"]


# LAST — see the header. An untargeted build is the development image.
FROM dependencies AS development

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
