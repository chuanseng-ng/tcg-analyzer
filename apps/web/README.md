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

| Path            | Contents                                                                                                                |
| --------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `app/`          | App Router routes. `/` is the landing page; `/analyze` is an M2 placeholder                                             |
| `app/cards/`    | `/cards` (search) and `/cards/[cardId]` (detail) — the catalog browse surface                                           |
| `app/identify/` | `/identify` — the identification-confirmation gate (spec §20)                                                           |
| `components/`   | `Container` and `Stack` layout primitives, and `ApiStatus`                                                              |
| `lib/`          | `api.ts` — the client for `services/api`; `card-search.ts`, `card-display.ts`, `card-errors.ts` and `identification.ts` |
| `styles/`       | `tokens.css` (design tokens) and `globals.css` (reset)                                                                  |
| `tests/`        | Vitest + React Testing Library, jsdom environment                                                                       |

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

Browsing leads exactly one place: the confirmation gate below. A card's own page
carries a single **This is my card** link into `/identify`, and nothing — not a
search row, not the detail page — offers any route past it into analysis.

## The confirmation gate

`/identify?card_id=<uuid>` is where the user says that this is the card in their
hand. Spec §20 is the whole reason it exists: _the user must confirm the result_,
and _never silently use an uncertain card identification for economic analysis_.
It is a product-integrity gate rather than a convenience screen, which is why it
has a route of its own instead of a button somewhere in the catalog.

- **In M1 the candidate arrives by manual selection**, because nothing produces
  a detected card until M2's image pipeline. The gate therefore reports the
  honest state — _Identification confidence: Not measured_ — and says who chose
  the card. It is deliberately **not** rendered as `0%`: zero is a measurement
  claiming the card is certainly not this one, and no measurement was taken.
- **There is no auto-confirm, at any confidence.** The only transition into the
  confirmed state is the user's tap, written as a functional state update that
  cannot fire from any other state. A 99% match would still take a tap.
- **`lib/identification.ts` mirrors the domain's `Uncertain[CardIdentification]`**
  — either a card with a validated `Confidence`, or a standalone
  insufficient-information result carrying no card at all. "An identification
  with insufficient-information confidence" is not representable there and must
  not become representable here. `manuallySelected` is M1's only producer and
  `identifiedFromImage` is the seam M2 feeds, so M2 adds a producer rather than a
  screen. No candidate is ever fabricated in order to reach a layout.
- **No confidence threshold is invented.** Nothing in the spec or the ADRs
  calibrates one, and a threshold is not what makes the screen safe — the
  question in the heading and the required tap are. The only distinction the
  wording draws is whether a measurement exists at all.
- **Confirming is not persisted.** It lives in React state on that page, and the
  screen says so out loud. There is no consumer of a confirmed identification
  until M2, and inventing a store for a consumer that does not exist would be
  the wrong shape to inherit.
- **The gate shows no `metadata` and no provider identifiers.** They are catalog
  bookkeeping rather than something a person checks against a card in their
  hand; the full record is one link away.

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
