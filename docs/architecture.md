# Architecture

This document is the contributor-facing record of how TCG Grading Advisor is put
together and which constraints are not open for negotiation. It exists so that
the shape of the system is legible from the repository alone.

Decisions that were genuinely open, and how they were settled, live in
[`docs/adr/`](adr). This document describes the constraints those decisions
operate under.

## The master architectural rule

> **The AI predicts the physical/graded outcome. The economic engine decides
> whether that outcome is worth pursuing.**

Everything below is downstream of that sentence. It is expected to survive every
future version of the application, so a change that blurs the boundary is a
change to the architecture, not a refactor.

## Domain architecture

```text
                         Web Application
                              │
                              ▼
                             API
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ▼               ▼                ▼
       Card Data Service  Analysis Service  Market Service
                              │
                              ▼
                         ML Pipeline
                              │
                  ┌───────────┼───────────┐
                  ▼           ▼           ▼
                 PSA         TAG         BGS
               Predictor   Predictor   Predictor
                  │           │           │
                  └───────────┼───────────┘
                              ▼
                       Economic Engine
                              │
                              ▼
                         Recommendation
```

## The analysis pipeline

End to end, from the photographs a user uploads to the answer they are given:

```text
                         USER
                          │
                     Card Images
                          │
                          ▼
                  Image Processing
                          │
                          ▼
                 Card Identification
                          │
                          ▼
                  Neutral Condition
                     Representation
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
             PSA         TAG         BGS
           Predictor   Predictor   Predictor
              │           │           │
              └───────────┼───────────┘
                          ▼
                 Grade Probability
                    Distributions
                          │
                          ▼
                    Market Data
                          │
                          ▼
                   Economic Engine
                          │
                          ▼
                Optimization Strategy
                          │
                          ▼
                    Recommendation
```

Image processing is itself a chain, and each step may refuse to continue:

```text
Original image → file validation → image quality → card detection
  → perspective correction → crop → normalization → card identification
  → template-aware analysis
```

Two of those steps are gates rather than transformations, and both are allowed
to stop the pipeline:

- **The image-quality gate.** An unusable image stops the analysis; a merely
  poor one continues, and the user is told which measurements it weakened. The
  gate is not advisory — a confident grade prediction drawn from an image nobody
  could grade by eye is exactly the failure this product must not produce.
- **Card identification.** A low-confidence match is never used silently. The
  user confirms the card, because every downstream number — market value,
  expected graded value, the recommendation itself — is wrong if the identity is
  wrong.

Market data is read from a **pre-ingested snapshot**. No user request calls an
external provider: providers are rate-limited, occasionally unavailable, and
their terms may restrict live use, while an analysis has to remain reproducible
from a recorded snapshot identifier long after the request is over.

## Architectural invariants

These are mandatory constraints. Violating one is not a trade-off to be argued
in review; it is a change of architecture, and the specification changes first.

### Grades are distributions, not points

Every grade prediction is a probability distribution over grades, retained in
full even when the interface shows a single expected grade. The API rejects
model output where any `P(g)` falls outside `[0, 1]`, or where `Σ P(g)` is not
approximately 1.

Enforced today by `GradeDistribution` in
[`packages/domain`](../packages/domain), which is framework-free precisely so
that the API, the analysis service and every ML module validate against one
implementation rather than three drifting copies.

### Condition is separate from grading-company prediction

```text
images → one neutral condition representation → {PSA model, TAG model, BGS model}
```

Each grading company gets its own model. There is deliberately no universal
`condition_score → grade` mapping, because the companies do not agree: the same
card is a different grade at PSA, at TAG and at BGS, and collapsing that into
one number discards the disagreement the product exists to surface.

### Grading is separate from economics

The ML system answers *"what grade might this receive?"*. The economic engine
answers *"is grading worth it?"*. Neither may depend on the other's internals.
This is the master rule above, restated as a code boundary.

### External providers are replaceable

Market data, card databases and marketplaces sit behind interfaces —
`MarketDataProvider`, `GradingCompanyAdapter` — and no provider may become a hard
dependency of the core domain. The pattern already in the tree is the
`ObjectStorage` port in [`packages/shared`](../packages/shared): the domain
imports the port, only the adapter module imports the vendor SDK, and a test
enforces that separation. See
[ADR 0002](adr/0002-object-storage-behind-a-port.md).

TCGplayer API access is neither assumed nor hard-coded. Access is not currently
granted, and its terms restrict competing commercial products.

The canonical card catalog is sourced from TCGdex under its MIT licence, entered
through `card_external_ids` so it stays replaceable, and imported without card
images — see [ADR 0004](adr/0004-the-canonical-card-catalog-source.md). The
market-price provider is a separate, still-open decision.

### Everything is versioned and immutable

Grading rules, model bundles, dataset versions and market snapshots are
versioned and never overwritten. A historical analysis keeps the exact versions
it used, so it can be re-derived rather than re-guessed. Models are referenced by
explicit version — `grading-psa-v0.2.0` — never by `/latest/`.

Every analysis records: application version, model bundle version, card database
version, grading rules version, market snapshot ID, economic configuration, and
the input image hashes.

### Uncertainty is a valid output

`insufficient_information` is a legitimate result for a surface analysis and for
the recommendation itself. The system never fabricates certainty, and never
forces a recommendation when the inputs cannot support one.

A mediocre model with honest uncertainty beats a sophisticated one that is
confidently wrong. `Confidence` and `InsufficientInformation` in
[`packages/domain`](../packages/domain) make that representable in the type
system rather than by convention.

### Provenance gates training data

Every training image needs a documented source, licence and commercial-use
right. The training pipeline rejects images whose commercial-use status is
unknown. Public accessibility is not permission, and a smaller dataset that is
legally usable is worth more than a large one that is not.

No model weights and no card photography are committed to this repository —
enforced by `tests/test_repository_structure.py`.

### The card domain is TCG-agnostic

V1 ships Pokémon only. Nothing internal may hard-code that: a card carries its
game, and Pokémon is a value of that field rather than an assumption baked into
a schema or a code path.

## Component map

```text
apps/           web, annotation
services/       api, analysis, market-data, ingestion
ml/             card-detection, card-identification, image-quality, centering,
                corners, edges, surface, condition, grading/{psa,tag,bgs},
                evaluation
packages/       domain, grading-companies, market-data, economic-engine, shared
database/       migrations, seeds, fixtures
datasets/       schemas, manifests, documentation
infrastructure/ docker, local, deployment
```

These are **logical boundaries, not microservices**. V1 deploys as one API and
one web application; the tree exists so the seams are in the right places when
something eventually has to move, not so that everything is separately
deployable now.

The split by language is not decorative either. `apps/*` is a pnpm/TypeScript
workspace; `packages/*`, `services/*` and `ml/*` are a uv/Python workspace,
because the domain invariants are imported by Python callers and must exist
exactly once. The frontend obtains its API types by generating them from the
FastAPI OpenAPI schema — see
[ADR 0001](adr/0001-language-boundaries-in-the-monorepo.md).

Background processing is required rather than optional: ML inference is
long-running and must never block an HTTP request. An analysis request returns a
job identifier, and the client polls for status.

## V1 boundary

**In scope.** Pokémon, English and Japanese. PSA, TAG and BGS. Front and back
upload from a mobile camera or a desktop. Centering, corners, edges, surface and
manufacturing defects where detectable. Grade distributions per company. SGD
economics with configurable grading fees, shipping, insurance, miscellaneous
costs and selling fees. Anonymous sessions, mobile-first.

**Explicitly excluded — do not build these.** Authentication, user accounts,
collections, portfolio management, social features, counterfeit detection, slab
analysis, crack-and-resubmit analysis, guided photography, defect visualization
in the main UI, grading submission, direct card selling, CGC, ARS, other TCGs,
global currencies, regional cost presets, and monetization.

The architecture must accommodate every one of them later without a rewrite.
That is the reason for the provider interfaces, the versioning discipline and
the TCG-agnostic card domain — the exclusions are a scope decision for V1, not a
statement about what the system may ever do.

## What this product is not

It is not an official grading service, and it does not authenticate cards in V1.
Predictions are probabilities and must never be presented as guaranteed grades.
That is a product-integrity requirement, and it constrains the interface as
firmly as anything above constrains the code.

## Recording a decision

When a deliberate architectural decision is made — or one the specification left
open is settled — write an ADR in [`docs/adr/`](adr), starting from
[`docs/adr/template.md`](adr/template.md). Numbered, dated, and not rewritten
after acceptance: a superseded decision earns a new ADR that says so, because
the reasoning that was live at the time is the part worth keeping.
