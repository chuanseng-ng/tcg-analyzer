# `apps/web`

The Next.js + TypeScript + React application: upload, card confirmation,
results and economics. **Mobile-first** — the primary input device is a phone
camera.

Types for API responses are generated from the FastAPI OpenAPI schema rather
than shared through a TypeScript package. See
`docs/adr/0001-language-boundaries-in-the-monorepo.md`.

Bootstrapped in M0 (#14).

## Commands

```bash
pnpm --filter @tcg/web dev        # http://localhost:3000
pnpm --filter @tcg/web build
pnpm --filter @tcg/web start
pnpm --filter @tcg/web lint
pnpm --filter @tcg/web test       # vitest run
pnpm --filter @tcg/web typecheck  # next typegen && tsc --noEmit
```

## Layout

| Path          | Contents                                                                    |
| ------------- | --------------------------------------------------------------------------- |
| `app/`        | App Router routes. `/` is the landing page; `/analyze` is an M2 placeholder |
| `components/` | `Container` and `Stack` layout primitives, and `ApiStatus`                  |
| `lib/`        | `api.ts` — the client for `services/api`                                    |
| `styles/`     | `tokens.css` (design tokens) and `globals.css` (reset)                      |
| `tests/`      | Vitest + React Testing Library, jsdom environment                           |

## Styling

CSS custom properties plus CSS Modules — no utility framework, no CSS-in-JS.
`styles/tokens.css` is the single source of colour, spacing, type scale, radii
and layout widths, and it is deliberately plain CSS so the results UI in M9 and
its chart palette can read the same values without a build step. Colours are
defined on bare `:root` and only _redefined_ under
`@media (prefers-color-scheme: dark)`.

**Mobile-first is a requirement, not a preference** — the primary input device
is a phone camera. Layout primitives use fluid units and `max-width` only;
nothing declares a fixed pixel width. A `tests/layout-primitives.test.ts` check
enforces that, standing in for a real 375px viewport assertion until E2E
arrives.

## Configuration

`NEXT_PUBLIC_API_BASE_URL` — base URL of the FastAPI service, defaulting to
`http://localhost:8000`. Copy `.env.example` to `.env.local`; never commit a
real `.env`.

The landing page reports API reachability through `/health` and degrades to an
"unreachable" state when the service is down. It never blocks the page.

## Notes for later milestones

- `lib/api.ts` types are **hand-written** against the frozen `/health`
  contract. #21 replaces them with types generated from the FastAPI OpenAPI
  schema, per ADR 0001. Do not grow the hand-maintained surface.
- `next.config.mjs` is plain ESM rather than `next.config.ts`: Next 15's
  TypeScript config loader does not resolve in this workspace.
- `typescript` is pinned to 5.x. TypeScript 7 removed `ts.sys`, which Next 15
  needs to read `tsconfig.json`; with 7.x installed, path aliases silently stop
  resolving during a build.
