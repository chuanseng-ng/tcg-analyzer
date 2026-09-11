# ADR 0012 — The beta runs from a Compose overlay on one host

- **Status:** accepted
- **Date:** 2026-09-11
- **Refs:** M10, #273, #263, #268, #269, spec §56, §75

## Context

Nothing deploys. The one Compose file is the local development stack, and its
shape is deliberately a developer's ([ADR 0003](0003-the-local-development-stack.md)):
no `networks:` block, every datastore published to the host, MinIO's root
credentials in the API and the worker, a worker whose filesystem is writable so
the file sync can reach it, and a broker spoken to over `redis://`. The security
review (#263, [`docs/security-review.md`](../security-review.md)) found spec
§56 *not met* on two of its four lines against that file, and left a ten-line
checklist on #273 with nowhere to land.

Spec §75 leaves the deployment mode open:

> V1:
>
> ```text
> Web
>  ↓
> Cloud API
>  ↓
> Local-development-compatible ML container
> ```

Three options were put on 2026-09-05, while M10 was being decomposed:

- **A full deployment with a platform ADR in M10.** That means choosing a host,
  DNS, backups and monitoring before there is one measurement of the traffic or
  the cost they would be sized against. Once chosen, a platform is very hard to
  switch away from.
- **No deployment work.** §56 stays *not met*, and the review's findings stay
  findings.
- **An overlay only.** This was chosen.

The overlay itself had two alternatives:

- **A second, standalone production Compose file.** It drifts from the local
  one the first time a service changes in only one of them.
- **Kubernetes or Terraform.** Either would be a second orchestrator for a
  system with one worker, and #273 excludes both.

## Decision

**The beta runs from `infrastructure/deployment/docker-compose.prod.yml`,
layered on the local file (`-f local -f prod`), on one host running Docker ≥ 26.**
The host, DNS, backups and monitoring are not chosen here. The overlay is the
file a platform runs, not a choice of platform.

1. **One hostname, one published service.**
   - Caddy terminates TLS.
   - It serves the web application at `/` and the API under `/api` with the
     prefix stripped. Web's `/cards` is the API's `/cards` too, so the two
     cannot share one path space unprefixed. Same origin also keeps the
     session cookie first-party.
   - `/api` is an allow-list, so `/internal/*`, `/docs` and `/openapi.json` are
     never routed ([ADR 0009](0009-the-dataset-store-as-a-database-domain.md)).
   - The proxy overwrites `X-Forwarded-For`, and the API is told
     `TCG_API_TRUSTED_PROXY_COUNT=1` (#269, the addendum to
     [ADR 0005](0005-rate-limiting-the-analysis-endpoints.md)).
2. **Three networks, and the application has no route out.**
   - `backend` (internal) holds the datastores, the worker and the API.
   - `edge` (internal) holds the proxy, the web application and the API.
   - `public` holds the proxy, plus the catalog import when an operator runs
     it. That import is the one command that needs the internet (ADR 0004).
3. **The worker meets §56 by construction.**
   - It keeps the local file's `cap_drop: ALL`, `no-new-privileges` and uid
     1001.
   - It is on `backend` only.
   - Its root filesystem is read-only, with `/tmp` on a tmpfs for beat's
     schedule.
   - It has a memory limit and a process limit.
   - It runs as **one replica**: beat is embedded, so a second replica is a
     second scheduler.
4. **No shared credentials.**
   - The API and the worker hold a MinIO account scoped to the one bucket,
     never root.
   - The broker is TLS-only, verified against a private CA that the stack
     generates on its first `up`. The CA's key never leaves the container that
     made it.
   - Every secret is required, so a missing one stops the stack instead of
     falling back to the local file's obvious defaults.
5. **The annotation tool is off by default**, behind a Compose profile. It is a
   browser client of `/internal`, which this proxy never routes, so the
   separate ingress ADR 0009 describes is not built here.
6. **The web image is built per domain.** Next inlines `NEXT_PUBLIC_*` at build
   time, so the API's URL is a build argument (#268). Changing the domain means
   a rebuild.

## Consequences

**What this makes easy.**

- Any host with Docker runs the beta from the repository with one command and
  a filled `.env`.
- The overlay cannot drift from the local stack, because it only states what
  differs.
- §56's four lines, and the review's ten, are properties of running
  containers. CI asserts them there (the `compose-production` job), and
  `tests/test_compose_production.py` asserts the rendered shape.

**What this makes expensive.**

- **One host is one host's CPU.** Scaling the worker out first means giving
  beat a process of its own. Losing the host loses the service until another
  one is brought up.
- **The state is three named volumes and nothing backs them up:**
  `postgres-data`, `minio-data` and Caddy's certificates. `down -v` destroys
  all three. Backups are the later ADR's.
- **The web image is per deployment,** so there is no single published image
  to promote from one environment to the next.
- **Some operator commands are not this host's.**
  - The dataset and evaluation commands serve the training corpus, which lives
    in its own database on a developer's machine.
  - The annotation tool is not reachable.
  - A command that needs the internet needs a service of its own, as the
    catalog import has.
- **The worker's limits are placeholders** (`mem_limit: 2g`, `pids_limit: 512`)
  until `docs/observability.md` has timed a real photograph.
- **The broker's private CA is valid for ten years and is rotated by deleting
  its volume.** Nothing outside this host trusts it, and nothing needs to.

**What this forecloses.**

Nothing permanently. A platform ADR may run this overlay, translate it, or
replace it. What it does decide is the order: a platform is chosen against a
running beta, not before one.

Revisit when:
- a platform is chosen;
- the worker needs a second replica;
- photographs retained under #148 need labelling on the host;
- the first real photograph is timed, which sets the limits.
