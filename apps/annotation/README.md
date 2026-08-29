# `apps/annotation`

Internal annotation tool for labelling training images: centering
measurements and corner, edge and surface defects.

**No grade and no condition score.** Neither annotation table carries one, and a
test asserts their absence: M7 derives spec §13's neutral condition
representation _from_ these rows, and M8 predicts a grade from that. What is
recorded here is what a person can see on the card.

Not a public surface. It is an internal tool and must never be exposed with the
consumer application.

Next.js + TypeScript + React on `apps/web`'s stack — one toolchain, one lint
config, one test runner. Types come from the FastAPI OpenAPI schema rather than
from a shared package ([ADR 0001](../../docs/adr/0001-language-boundaries-in-the-monorepo.md)).

## Commands

```bash
pnpm --filter @tcg/annotation dev        # http://localhost:3001
pnpm --filter @tcg/annotation build
pnpm --filter @tcg/annotation lint
pnpm --filter @tcg/annotation test       # vitest run
pnpm --filter @tcg/annotation typecheck  # next typegen && tsc --noEmit
pnpm --filter @tcg/annotation gen:api-types
```

It reads a running `services/api`. Copy `.env.example` to `.env.local` for a
bare `pnpm dev`; the Compose stack passes the root `.env` instead.

There is nothing to look at until a corpus exists:

```bash
uv run tcg-ingest-training-images --front front.jpg --back back.jpg …
uv run tcg-normalize-training-images
```

## How it is kept internal

The tool reads `/internal/annotation` on the same FastAPI application the
consumer product uses. That is [ADR 0009](../../docs/adr/0009-the-dataset-store-as-a-database-domain.md)'s
decision, not an oversight: spec §7 says not to create unnecessary
microservices in V1, and a second FastAPI application would duplicate the
session, error-envelope and migration wiring in order to enforce a boundary the
deployment already enforces.

**The isolation is deployment topology.** `/internal/` is a prefix an ingress
rule matches, and this application is served from an origin the public one does
not route to. There is no authentication and there should not be: V1 excludes
accounts outright, and an internal tool reached over a private network is the
shape this repository already assumes. Two things would reopen that — annotation
traffic that cannot be separated at the edge, or a second party annotating —
and either is a new ADR.

`tests/no-object-store.test.ts` holds the other half of the boundary: nothing
here talks to object storage. The bytes arrive from the API, and `lib/env.ts`
reads one variable, so this application knows exactly one origin.

## What it shows, and why the badge matters

**The normalized artifact, not the photograph.** `ml/normalization` warps a
photograph to a fixed 756×1056, and an annotation is stored as a _fraction of
that artifact_. A corner marked at 12% across a raw photograph says nothing
comparable about the card, because the next photograph is framed differently.

Where no card could be located there is no artifact, and the tool shows the
photograph with the difference stated on screen and in the `alt` text. That is
the honest degradation: the frame is still worth looking at, and a coordinate
taken against it would not be.

## Keyboard

This is a tool somebody uses for hours, so the frame is focusable and every
action has a key as well as a button.

| Key                                                 | Action                                           |
| --------------------------------------------------- | ------------------------------------------------ |
| <kbd>←</kbd> <kbd>→</kbd> <kbd>↑</kbd> <kbd>↓</kbd> | Pan; hold <kbd>Shift</kbd> to move further       |
| <kbd>+</kbd> <kbd>−</kbd>                           | Zoom about the centre                            |
| <kbd>0</kbd>                                        | Fit                                              |
| <kbd>1</kbd>                                        | Actual size, one artifact pixel per screen pixel |
| <kbd>f</kbd>                                        | The other view of this copy                      |
| <kbd>c</kbd> <kbd>e</kbd> <kbd>s</kbd>              | Arm the corner, edge or surface tool             |
| <kbd>m</kbd>                                        | Arm the centering measurement                    |
| <kbd>Esc</kbd>                                      | Back to panning                                  |

**Four letters and an escape, and no more.** §14, §15 and §16 come to
twenty-eight labels between them, and a mnemonic scheme for that many would be a
second vocabulary free to drift from the schema's — so choosing a _tool_ is a key
and choosing a label is a `<select>`. None of them is a digit, because
<kbd>1</kbd> is already actual size.

Paging between images is the work list, whose rows are ordinary links and
therefore reachable with <kbd>Tab</kbd> and <kbd>Enter</kbd>. After a save the
tool goes to the next image awaiting annotation by itself.

## Annotating

**Nothing is written until you say so.** Both annotation tables refuse an
`UPDATE`, so a marker written in error cannot be corrected — only added to. Work
is therefore staged in the browser, where it can be removed, and one Save writes
all of it in one transaction. Leaving the view or the tab with work staged asks
first.

**One save writes one image.** The front and the back of a card are two rows, and
the side toggle changes which one the frame is showing without navigating — so a
marker belongs to the view it was drawn on, and switching views with work staged
asks before discarding it. After a save the tool takes you to the next image
awaiting annotation: a sibling of the same copy first where one is still
unannotated, and otherwise the head of the queue.

**Centering is measured, not typed.** Drag a box round the inner frame and the
two ratios follow from where its edges sit — the artifact's edges _are_ the
card's, so the borders are what is left outside the box. Either axis can be
switched off for spec §21's full-art and borderless layouts, which stores `null`
rather than a fabricated `0.5`.

**Uncertainty is one action.** _I cannot tell_ records the `unknown` label every
vocabulary carries, which needs no severity, so admitting it costs one click
where guessing costs three. That is deliberate: if the admission is the slower
path, the corpus fills with confident guesses, and a model trained on those is
the confidently-wrong output this product's invariants forbid. Nothing
pre-selects a confidence either — the column is NOT NULL with no default on
purpose, and a checked radio would put that default back where the schema cannot
see it.

The annotator and the timestamp are the service's. There is nothing to type and
nothing to choose: `TCG_API_ANNOTATOR_ID` names the annotator, and a request that
tries to name one is refused.

## What the artifact can and cannot resolve

`ml/normalization` warps to `pixels_per_mm = 12`, which is 756x1056 on a 63x88 mm
card — exactly 63:88, and about 305 dpi. One artifact pixel is **83 microns**.
Against that, the defect classes §30 asks an annotator to mark are not equal.
The question was settled — by arithmetic where arithmetic sufficed, and by
measurement against real photographs where it did not — in
[ADR 0010](../../docs/adr/0010-what-surface-defects-are-measured-against.md)
(#171):

| What is being judged           | Size on the card         | At 12 px/mm    |                              |
| ------------------------------ | ------------------------ | -------------- | ---------------------------- |
| Centering, 55/45 vs 60/40      | ~0.3 mm on a 6 mm border | ~3.6 px        | adequate                     |
| Corner whitening, just visible | ~0.2–0.5 mm              | 2.4–6 px       | adequate — **measured**      |
| Print line                     | ~50–200 µm               | 0.6–2.4 px     | marginal                     |
| Hairline scratch               | ~10–50 µm                | **0.1–0.6 px** | **below the sampling limit** |

(The first column is arithmetic; the second is an estimate of physical defect
sizes and is the part to argue with.)

**Surface defects are settled, and negatively.** A hairline scratch is smaller
than one artifact pixel, so no amount of looking at a screen changes the answer —
§16's `scratch`, `print_line`, `print_dot` and `gloss_issue` cannot be marked
reliably against this artifact. That is a finding about
[`ml/normalization`](../../ml/normalization), not about this viewer. **The surface
tool says so on screen**, next to the control, because that is where somebody is
when the question arises — and _I cannot tell_ is right there beside it.

**Corners were the empirical question, and real photographs settled it**
(ADR 0010's evidence): worn-corner extent was judgeable at 12 px/mm, and
24 px/mm added only slight detail — not worth 4× the pixels and a
`NORMALIZATION_VERSION` bump that would recompute every artifact and
fingerprint.

**Centering is fine**, which matters because it is the one measurement §21
defines numerically.

### The decision — ADR 0010

**12 px/mm stands.** Raising the resolution was measured and declined; the fine
surface classes it would have existed to rescue are not rescued by any rate a
real photograph supports (a hairline scratch is still under 2 px at the source's
own ~34–36 px/mm). The one route back to a reliable fine-class surface signal
is **#175** — annotating surface against the _original photograph_, with the
representation named on the row — which is a schema and UI change of its own,
not a threshold.

## Layout

| Path          | Contents                                                                            |
| ------------- | ----------------------------------------------------------------------------------- |
| `app/`        | `/` is the work list; `/images/[imageId]` is the viewer                             |
| `components/` | `Container` and `Stack` — **copied** from `apps/web`, see below                     |
| `lib/`        | `api.ts`, generated `api-types.ts`, `env.ts`, `annotation-errors.ts`, `viewport.ts` |
| `styles/`     | `tokens.css` and `globals.css` — **copied** from `apps/web`                         |
| `tests/`      | Vitest + React Testing Library, jsdom                                               |

`lib/viewport.ts` holds pan and zoom as pure functions, deliberately: the one
property that matters — the image never leaves the frame, at any scale, after
any pan — is a claim about numbers, and `tests/viewport.test.ts` sweeps it
directly rather than asserting jsdom's layout.

## The duplication, stated plainly

`components/`, `styles/` and `lib/env.ts` are **copied from `apps/web`, not
shared.** ADR 0001 gives the two applications no TypeScript package in common —
their contract is the OpenAPI schema — so extracting them would mean a new pnpm
package and a build step for a handful of declarations.

The cost is real and worth naming: a token changed in one app does not reach the
other, and **nothing detects the drift**. Two applications is where copying is
cheaper than the seam. A third is where `packages/ui` earns its own ADR.

Populated in M6 — dataset platform.
