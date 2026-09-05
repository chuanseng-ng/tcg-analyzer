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

| Path             | Contents                                                                                                                                                                                                                                             |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/`           | App Router routes. `/` is the landing page                                                                                                                                                                                                           |
| `app/analyze/`   | `/analyze` — photograph the front and back of a card and upload them (spec §48)                                                                                                                                                                      |
| `app/cards/`     | `/cards` (search) and `/cards/[cardId]` (detail) — the catalog browse surface                                                                                                                                                                        |
| `app/identify/`  | `/identify` — the identification-confirmation gate (spec §20)                                                                                                                                                                                        |
| `app/configure/` | `/configure` — the economic configuration screen (spec §45, §46, §43)                                                                                                                                                                                |
| `app/results/`   | `/results` — the recommendation, the expected economic outcome, each company's grade distribution and the company comparison (spec §49, §44, §41, §2.1, §43)                                                                                         |
| `components/`    | `Container` and `Stack` layout primitives, and `ApiStatus`                                                                                                                                                                                           |
| `lib/`           | `api.ts` — the client for `services/api`; plus `card-*.ts`, `identification.ts`, `upload-*.ts`, `confirm-errors.ts`, `economics-errors.ts`, `results-errors.ts`, `results-copy.ts`, `analysis-state.ts`, `amount-input.ts` and `analysis-session.ts` |
| `styles/`        | `tokens.css` (design tokens) and `globals.css` (reset)                                                                                                                                                                                               |
| `tests/`         | Vitest + React Testing Library, jsdom environment                                                                                                                                                                                                    |

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

## The upload screen

`/analyze` is where the product actually begins: photograph the front and the
back of a card and commit them to an analysis. The landing page's spec §48 call
to action has always pointed here. Six decisions shape it:

- **Nothing is sent until the user says so.** Both photographs are staged in the
  browser and uploaded by one explicit action. That is what makes **Remove**
  possible at all — spec §65's state graph is forward-only, so once an analysis
  holds an image no legal move takes it back, and a photograph that has not been
  sent is the only one that can be un-chosen. After the upload the corrections
  are **Retake**, which `POST /analyses/{id}/images` treats as a replacement, and
  **Start over**, which abandons the analysis for a fresh one.
- **One native file input per side, with no `capture` attribute.** `capture`
  forces the camera and _removes_ the photo library and the file picker; without
  it mobile Safari and Chrome offer Take Photo, Photo Library and Browse from the
  same control, and a desktop gets the file picker. `accept` names JPEG and PNG
  concretely rather than `image/*`, because iOS hands over an unconverted HEIC
  for `image/*` and the service refuses it after the whole file has arrived.
- **Front and back are never told apart by position alone.** The side is in the
  heading, the button, the alt text and the status line. Mixing the two up
  silently corrupts centering and condition analysis, and no later stage can
  notice.
- **`lib/api.ts` uploads over `XMLHttpRequest`, not `fetch`.** `fetch` has no
  upload-progress event: reporting one needs a streaming request body, which
  Safari does not implement. Spec §48 lists upload progress as a requirement, so
  the transport follows the requirement. A failure is the same `ApiError` either
  way.
- **`lib/upload-slots.ts`'s size and type rules are a courtesy.** They save a
  wasted upload over a mobile connection; `services/api` sniffs the content and
  is the only thing that decides (spec §55). There is deliberately no pixel
  check — reading an image header means decoding the file, which is client-side
  image analysis and a non-goal.
- **Where it leads, it leads on a tap.** Once both photographs are stored,
  **Choose which card this is** runs the analysis (`POST /analyses/{id}/run`)
  and goes to the catalog. Nothing navigates on its own, and the run happens
  first — which is why it is a button rather than a link. A 409 from the run
  means the analysis is already past `uploaded`, which is where the button was
  trying to get it, so the hand-off continues rather than reporting an error.
- **The analysis identifier travels in `sessionStorage`** (`lib/analysis-session.ts`),
  because the trip to `/identify` passes through `/cards` and `/cards/[cardId]`.
  Carrying it in the URL would mean re-emitting it from the search form, the
  pager, every result row and the detail link — and it would buy nothing, since
  an analysis id is worthless without the HTTP-only `tcg_session` cookie. It is
  a convenience, never authorisation: the API scopes every analysis to that
  cookie.

`lib/upload-errors.ts` is a sibling of `lib/card-errors.ts` rather than an
extension of it: two of its outcomes — throttled, and an analysis that has moved
past taking photographs — have no counterpart at `GET /cards/{id}`. A 429 is
answered with the `Retry-After` countdown and **no** retry button, because a
button there fires straight back into the limit that produced it.

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
- **Confirming is recorded against the analysis, when there is one.** The
  identifier comes from `sessionStorage`, left there by `/analyze`, and the tap
  calls `POST /analyses/{id}/confirm-card`. Arriving from the catalog with no
  photographs is still a legitimate path, and then the confirmation lives on the
  page alone and the screen says so. A confirmation that does not reach the
  service is **not** a confirmation: the screen returns to the question with
  what went wrong, rather than showing a confirmed state nothing recorded.
  `lib/confirm-errors.ts` is a third sibling of `card-errors.ts` and
  `upload-errors.ts`, because a 409 here means "your photographs are not ready
  for this yet" where the upload's means "start a new analysis".
- **There is no route into analysis** in any branch, including the failures:
  `/analyze` is where an analysis begins and this gate is not a second door into
  it. Forwards is different. Once the confirmation has actually been recorded,
  spec §5's next step exists, so the screen offers **Set the costs** and advances
  to `/configure` on its own after four seconds, with the link live throughout. A
  confirmation the page kept to itself gets neither: there is no analysis to
  price. The confirmed screen still says that nothing has analysed the
  photographs yet, because nothing has.
- **The gate shows no `metadata` and no provider identifiers.** They are catalog
  bookkeeping rather than something a person checks against a card in their
  hand; the full record is one link away.

## The configuration screen

`/configure` is where the user prices their own decision — spec §48's
Configuration screen, filled in against §45, §46 and §43. It is the step after
the confirmation gate, because `POST /analyses/{id}/economic-configuration`
accepts figures only while the analysis is `analyzing`, which is the state
confirming the card reaches. Four decisions shape it:

- **Blank means unknown, and never zero.** Spec §45 makes the acquisition cost
  optional and forbids inferring it. A field pre-filled with `0.00` would turn
  "I don't remember what I paid" into "it was free", and the investment return
  computed from that is not imprecise — it is a different, confidently wrong
  answer. So the field starts empty with no placeholder amount, a blank one
  reaches the wire as `null`, and `"0.00"` typed in is sent as the real
  acquisition cost it is. The screen says which of the two questions that costs
  the user, on the way in and again on the way out.
- **Spec §45's two questions are named apart, in the user's language.** _Is it
  worth grading this card?_ and _did this card make money?_ head the screen,
  above the one field that is the whole difference between them. The domain's
  own vocabulary — `incremental_roi`, `investment_return` — is not what a
  collector asks.
- **The costs carry no defaults of their own.** Every cost field on the request
  is optional and the endpoint fills it from the engine's `CostConfiguration`,
  which is the single place those figures are written down. Restating them here
  would be a second copy that drifts from the one the recommendation is computed
  against, silently and in the direction of the user's money. So the six fields
  sit blank inside a collapsed section, an untouched form sends no `costs` key at
  all, and the amounts that were actually used are read off the 201 and shown on
  the way out — which is the only place they are ever seen. `apps/web` does not
  know what they are, and must not learn.
- **The selling fee is asked for as a percentage and sent as a proportion.** The
  engine refuses `Decimal("10")` by name — ten percent is `0.10` — so
  `lib/amount-input.ts` shifts the decimal point rather than dividing by 100.
  Nothing on this screen converts an amount to a `number`: the service refuses a
  JSON number where money is meant, because it is a binary float in most clients.

Everything else follows the gate: one `lib/economics-errors.ts` classifier, a 429
counted down with no button (ADR 0005), and no total anywhere — §46's line items
are named and are never added into one figure. Recording the figures completes
the analysis, so the recorded view now leads on: a **See the results** link, live
throughout, and `/results` on its own after four seconds — `/identify`'s pattern.

## The results screen

`/results` is where the product finally answers — spec §49's first three
priorities, the recommendation, the expected economic outcome and the grade
distribution, then its second screen, the company comparison, in that order.
The condition block has its place held below and arrives with its own issue.
Eight decisions shape it:

- **The analysis is read first, and the results once.** `GET /analyses/{id}` is
  the endpoint §65 says a client polls, and `completed` means every input the
  results need is recorded — the configuration write reaches it, so arriving
  from `/configure` the first read already says so. The poll exists for a
  reload, a direct arrival and for `failed`; it runs at `/analyze`'s cadence,
  stops on a terminal state or when the screen is left, and `lib/analysis-state.ts`
  is the one place the web spells §65's nine state names.
- **"Not asked yet" and "not enough information" are two screens.** A `null`
  recommendation means no configuration or no stored prediction — nobody has
  asked. `insufficient_information` means the engine was asked and the data did
  not support an answer. With the V1 heuristics every recommendation is the
  second, on the grading model's confidence of 0.35 against a threshold of 0.50,
  and the screen shows that as an admission with those numbers in words — it
  neither hides the recommendation nor invents a verdict, and the companies'
  figures stay below it rather than behind it.
- **The reason is three things a person can check.** What was measured, what it
  came to, what it needed to clear — from the `figure`, `value` and `threshold`
  the wire carries — plus one sentence per `code`. Every gate that failed is
  listed, not only the decisive one. `lib/results-copy.ts` holds all of it, keyed
  off the code with a fallback that **names** an unknown code, because the codes
  are bare strings on the wire and an empty string would be a recommendation with
  no reason shown. Nothing is ever labelled `roi`.
- **The two §41 figures are named apart, as `/configure` named them.** _Is it
  worth grading this card?_ and _Did buying this card make money?_ head each
  company's block, each figure present-and-null beside its own reason and rendered
  as that reason — never as a number, never as zero — when null. A negative
  profit is an answer and carries its sign. Every amount is the decimal string the
  wire carried, prefixed with the currency, and nothing is a total.
- **Every figure sits beside its date.** The market snapshot's date and version
  are stamped under the companies (ADR 0006), and when the analysis recorded no
  snapshot the screen says so rather than showing an undated figure.
- **The grade distribution is drawn whole, and the chart is the table.** Spec
  §2.1 insists the distribution be kept, not collapsed to a grade, so each
  company gets horizontal bars for every grade on the wire, in the wire's order
  — `9.5` where a company issues one, a collapsed tail as `≤ 7` — with nothing
  sorted, bucketed or renormalised on the client. Spec §6 prints the block as
  two columns, so the chart is one `<table>` per company: the grade is the row
  header, the bar and its percent are the cell, and a screen reader hears the
  same ladder a sighted reader sees. Every bar is labelled to the whole percent;
  a probability that rounds to zero but is not zero reads `<1%` and keeps a
  visible bar. The design follows the `dataviz` skill: one series, one hue,
  thin bars from a hairline baseline, no legend, no tooltip because nothing is
  left unlabelled, and no charting library. The colour is `--color-series-1`
  in `styles/tokens.css`, validated in both colour schemes against this app's
  own surfaces — the accent was tried and fails the dark-mode lightness band —
  and the component carries no colour literal. The grading model's confidence
  sits beside each chart as the one number, in the copy table's words.
- **The comparison is the engine's order, and the markup says so.** Spec
  §49's "Compare PSA / TAG / BGS" is rendered from `recommendation.comparison`
  under the label the wire supplies for the mode chosen on `/configure` — the
  page never keeps its own copy of §43's five names. The ranked companies are
  an ordered list, each with the figure it was ranked on under the copy table's
  label (money or a percent by figure, never `roi`) and its confidence; the
  companies with no place in the order — no priced ladder, or a model that
  refused — are an unordered list apart, each with its reason in words, and
  are never appended as the last rows. There is no table, because the figure
  can differ per company under one mode (`P(10)` beside `P(9_or_higher)`) and
  a single column header would say otherwise. A tie at the top is said in a
  sentence, because the alphabetical order among tied companies means nothing.
  When nothing could be ranked — every V1 analysis, since nothing is priced —
  the section is the admission plus one line per company whose model refused,
  and an empty `refused` beside it is said as "nothing was priced", which is
  what it means, rather than as "nothing was refused".
- **`failed` is explained from the photographs.** The poll endpoint carries no
  error envelope; `confirm-card` decides between `image_quality_failure` and
  `analysis_failed` by whether any photograph is `unusable`, and this screen
  applies the same rule to the same field — naming the side and the gate's own
  words from `lib/quality-copy.ts` — rather than making a second request to learn
  a code. Any other failure is said without blaming the photographs.

`lib/results-errors.ts` is a fifth error sibling: the bare 404 is `restart`, a
store that would not answer is `retry`, and anything else is `unexpected`. Display
names come from `GET /grading-companies`; a listing that fails leaves the slugs on
screen rather than holding the figures back.

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
