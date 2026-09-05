# `.github/workflows`

GitHub Actions workflows. Together these enforce the Definition of Done
(spec §71), which is aspirational without them.

| Workflow | Runs on | Checks |
| --- | --- | --- |
| `ci.yml` | PR, push to `main` | ruff, mypy, pytest; eslint, prettier, OpenAPI type drift, tsc, vitest, `next build`; migrations against a fresh PostgreSQL; signed URLs against MinIO and one anonymous analysis driven through every endpoint; API image build; secret scan; dependency review |
| `codeql.yml` | PR, push to `main`, weekly | Static analysis for Python and TypeScript |
| `pr-title.yml` | PR opened or edited | Conventional Commits, since a PR title becomes the squash-merge subject |

**Bot PRs are held to the prefix only.** Dependabot writes its own subjects —
`Bump X from A to B in /dir` — and neither the capital letter nor the length is
configurable. The `type(scope):` prefix *is*, through `commit-message` in
`dependabot.yml`, so that part is still checked: a misconfigured prefix is the
half we own.

Dependency updates are configured in [`../dependabot.yml`](../dependabot.yml).

## Notes

**`/health` and `/readiness` are checked separately** because they answer
different questions — see `services/api`. The migrations job has a database and
no MinIO, the storage job has both, and the Python job deselects `-m integration`
and `-m object_storage` alike.

**The storage job runs the local Compose file** rather than a service container,
because MinIO needs a `server /data` command and a service container cannot
supply one. It also means `infrastructure/local/docker-compose.yml` is exercised
on every PR instead of only when someone clones the repository. Since #250 it
brings up Compose's PostgreSQL beside MinIO, because
`services/api/tests/test_anonymous_journey.py` uploads real photographs and runs
the real worker against them — the one module that needs both, and it carries
both markers and both skips so the migrations job passes over it.

**The secret scan reads full history**, not the diff. A credential removed from
the working tree is still leaked, and this repository is public.

**The OpenAPI drift check** regenerates `apps/web/lib/api-types.ts` and fails if
it differs from the committed file. Per
[ADR 0001](../../docs/adr/0001-language-boundaries-in-the-monorepo.md) the
schema is the frontend/backend contract, so a drift means the frontend is
compiling against a contract the server no longer honours. Fix it with:

```bash
pnpm --filter @tcg/web gen:api-types
```

**Reproducing a failure locally** — every job runs commands documented in the
root `README.md`. Nothing in CI is a step you cannot run yourself.

## Not here yet

- E2E tests (Playwright) arrive with the results UI in M9.
- No deployment pipeline. Publishing an image is a deployment concern; `ci.yml`
  only verifies that the image builds.
- ML training never runs in CI.
