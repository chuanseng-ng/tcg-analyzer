# ADR 0001 — Language boundaries in the monorepo

- **Status:** accepted
- **Date:** 2026-08-16
- **Refs:** M0, #12, spec §7, §8

## Context

Spec §7 mandates the directory tree but is silent on which language owns each
part of it, and §8 names both Next.js/TypeScript and Python/FastAPI without
saying where `packages/` sits. The scaffold needs an answer before any code
lands, because two workspace managers have to be configured and the choice is
expensive to reverse once packages have contents.

The constraint that settles it is `packages/domain`. Spec §63 requires that the
API reject model output whose grade distribution is invalid, and §2.1 requires
the distribution be retained in full throughout. `GradeDistribution` is
therefore constructed and validated by `services/api` (FastAPI, Python) and
produced by `ml/grading/{psa,tag,bgs}` (PyTorch, Python). `packages/economic-engine`
is likewise called by the API and consumes `GradeDistribution` and `Money`.

A TypeScript `packages/domain` could not be imported by any of those callers.
The invariant would have to be reimplemented in Python, which means the single
most load-bearing rule in the system would exist in two places and could drift.

## Decision

```text
pnpm workspace  →  apps/*                        TypeScript
uv workspace    →  packages/*, services/*, ml/*  Python
```

- `packages/{domain,grading-companies,market-data,economic-engine,shared}` are
  Python distributions in the `uv` workspace.
- `apps/{web,annotation}` are the only pnpm workspace members.
- `apps/web` obtains its API types by generating them from the FastAPI OpenAPI
  schema, not from a shared TypeScript package.
- Distribution names are `tcg-<path-with-dashes>`; ML modules take a `tcg-ml-`
  prefix. `services/market-data` is `tcg-market-data-service` because it shares
  a directory name with `packages/market-data`.

## Consequences

- The domain invariants exist exactly once, in Python, and are enforced
  identically wherever they are used.
- The frontend/backend contract is the OpenAPI schema. It must stay accurate,
  because it is now the only source of frontend types — this raises the cost of
  an undocumented endpoint, deliberately.
- Type safety across the HTTP boundary depends on a generation step. That step
  belongs to #14 and #21, and CI must fail if generated types are stale.
- A future TypeScript consumer of domain logic (a Node service, an edge
  function) would need the same generation approach rather than a direct
  import. No such consumer is planned in V1.
- This closes an ambiguity in issue #12's original scope line, which listed
  `packages/*` under the pnpm workspace. The issue was corrected to match.
