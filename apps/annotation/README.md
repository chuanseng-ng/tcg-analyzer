# `apps/annotation`

Internal annotation tool for labelling training images: centering
measurements, corner/edge/surface defects and grade ground truth.

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

Paging between images is the work list, whose rows are ordinary links and
therefore reachable with <kbd>Tab</kbd> and <kbd>Enter</kbd>. Advancing
automatically after a save belongs to the annotation controls, which are the
next issue.

## An open question about resolution

**Whether 756×1056 carries enough detail to judge a soft corner is not settled.**
A corner is roughly 3% of the artifact's width — about 22 pixels — and at the
viewer's maximum magnification that is around 180 screen pixels. Whitening on a
corner is a sub-millimetre change, and no real card photographs exist in this
repository to check it against ([ADR 0008](../../docs/adr/0008-permitted-training-image-sources.md)
makes `redistribution_allowed` false everywhere, so none may be committed).

Look at a real corner at maximum zoom before annotating a corpus in earnest. If
the artifact turns out to be the limiting factor, **that is a finding about the
normalization stage's output size, not something to work around here**: the
target lives in `ml/normalization`, changing it bumps `NORMALIZATION_VERSION`,
and no amount of CSS invents detail the artifact does not carry.

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
