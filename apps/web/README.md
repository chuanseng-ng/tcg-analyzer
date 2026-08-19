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

| Path          | Contents                                                                         |
| ------------- | -------------------------------------------------------------------------------- |
| `app/`        | App Router routes. `/` is the landing page; `/analyze` is an M2 placeholder      |
| `app/cards/`  | `/cards` (search) and `/cards/[cardId]` (detail) — the catalog browse surface    |
| `components/` | `Container` and `Stack` layout primitives, and `ApiStatus`                       |
| `lib/`        | `api.ts` — the client for `services/api`; `card-search.ts` and `card-display.ts` |
| `styles/`     | `tokens.css` (design tokens) and `globals.css` (reset)                           |
| `tests/`      | Vitest + React Testing Library, jsdom environment                                |

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

## The card catalog surface

`/cards` searches the catalog and `/cards/[cardId]` shows one card's canonical
record. Four decisions shape both, and none of them is arbitrary:

- **The URL is the state.** `/cards?text=charizard&set_code=BS&offset=20` is the
  whole of what the page is showing, so a search survives a reload, answers the
  back button and can be sent to someone else. `lib/card-search.ts` owns the
  three forms a query takes — the URL's, the form's and the API's — so no
  component has to know two of them at once.
- **Both pages fetch in the browser.** `NEXT_PUBLIC_API_BASE_URL` is the API as
  the _browser_ reaches it; a server render inside the Compose network cannot
  use that address, and adding a second base URL would be a second way for the
  two to disagree. The `Suspense` boundary in `app/cards/page.tsx` is therefore
  load-bearing: `useSearchParams` without one fails `next build`.
- **Searching needs an explicit submit.** Japanese input goes through IME
  composition, where a keystroke-debounced listener fires on half-composed kana
  and searches for text the user has not finished typing. `/cards/search` also
  has no rate limiting yet.
- **There is no card image, ever.** `docs/adr/0004-the-canonical-card-catalog-source.md`
  imports no artwork, so set, number, variant and rarity carry the
  disambiguation a picture would have done. Every result states its variant —
  holo, reverse holo and 1st edition are economically different cards — and an
  unrecorded variant says so rather than leaving a blank.

Nothing here leads to analysis. Confirming which card is in the user's hand is a
product-integrity gate with its own screen, and browsing must not become a way
around it.

## Configuration

`NEXT_PUBLIC_API_BASE_URL` — base URL of the FastAPI service, defaulting to
`http://localhost:8000`. Copy `.env.example` to `.env.local`; never commit a
real `.env`.

The landing page reports API reachability through `/health` and degrades to an
"unreachable" state when the service is down. It never blocks the page.

## Notes for later milestones

- `lib/api.ts` response types are **generated**, aliased out of
  `lib/api-types.ts`, per ADR 0001. Never hand-write one: add the endpoint to
  the FastAPI schema and run `pnpm --filter @tcg/web gen:api-types`. CI runs
  `gen:api-types:check` and fails if the two drift apart. The search
  endpoint's query parameters are read off the generated operation for the same
  reason, so a filter added server-side is a compile error rather than a
  silently ignored option.
- `next.config.mjs` is plain ESM rather than `next.config.ts`: Next 15's
  TypeScript config loader does not resolve in this workspace.
- `typescript` is pinned to 5.x. TypeScript 7 removed `ts.sys`, which Next 15
  needs to read `tsconfig.json`; with 7.x installed, path aliases silently stop
  resolving during a build.
