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
  Five of the eleven conditions it must decide — perspective distortion, card
  partly outside frame, multiple cards, sleeve obstruction, insufficient card
  size — are about *where the card is*, so card detection runs before the gate
  answers them even though §18 draws it after. That changes which stage measures
  what, never which stage owns the verdict: a photograph the detector cannot
  find a card in is reported honestly as five conditions unchecked, not refused
  and not passed.
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

The engine answers that question twice, never once: the incremental grading
decision and the investment return are separately named figures over separate
denominators, and an absent acquisition cost makes the investment figures `null`
rather than zero. See
[ADR 0007](adr/0007-roi-and-the-capital-at-risk-basis.md), which fixes the
`CapitalAtRisk` basis spec §42 requires be settled before ROI reaches the UI.

The boundary is executed rather than asserted. `packages/economic-engine` depends
on `tcg-domain` and nothing else, and a purity test proves it with a real import
in a fresh interpreter — no `tcg_ml_*`, no provider client. Spec §40's expected
value takes a `GradeDistribution` and a plain mapping of grade to price, so the
engine cannot ask which model produced the distribution or which provider
produced the prices. It is also entitled to refuse: a distribution whose grades
carry no price at all yields `insufficient_information`, and an unpriced grade is
excluded and recorded rather than silently valued at zero.

### External providers are replaceable

Market data, card databases and marketplaces sit behind interfaces —
`MarketDataProvider`, `GradingCompanyAdapter` — and no provider may become a hard
dependency of the core domain. The pattern already in the tree is the
`ObjectStorage` port in [`packages/shared`](../packages/shared): the domain
imports the port, only the adapter module imports the vendor SDK, and a test
enforces that separation. See
[ADR 0002](adr/0002-object-storage-behind-a-port.md).

`CardRepository` in [`packages/domain`](../packages/domain) is the same shape
applied to the catalog, and lives with the domain rather than in `shared`
because it speaks the domain's language — it returns a `Card`, where object
storage only moves bytes. It names no `dsn`, `pool` or `session`; the PostgreSQL
adapter that satisfies it lives in
[`services/api`](../services/api/src/tcg_api/catalog/cards.py), outside the
package, and translates every driver failure into the port's own
`CatalogUnavailable` so no route handler ever sees an asyncpg exception. The
port also fixes what a search means — a case-insensitive name fragment that
works for Japanese, a card number matched as a prefix, and a total order over
`(set_code, card_number, variant, id)` — so the guarantee belongs to the domain
rather than to whichever database happens to be answering.

`GradingCompanyAdapter` in [`packages/grading-companies`](../packages/grading-companies)
is the third, and the one where replaceability is a stated requirement rather
than a principle: adding CGC or ARS later must cost one new adapter and no
caller change. That is why the company vocabulary is a `StrEnum` whose members
are `str` — the shape `Game` and `Language` already use — instead of a closed
enum sitting between every caller and every adapter. Its V1 adapters answer
four of spec §22's five responsibilities from published reference data and
refuse the fifth: `predict_grade` raises rather than returning a fabricated
distribution until M8's per-company models exist. The three grade scales are
not interchangeable — PSA and TAG issue no 9.5 and BGS does — which is why spec
§35 keys a graded price by `(grading_company, grade)` and why this package
lands in M4 ahead of the models that will use it.

TCGplayer API access is neither assumed nor hard-coded. Access is not currently
granted, and its terms restrict competing commercial products.

The canonical card catalog is sourced from TCGdex under its MIT licence, entered
through `card_external_ids` so it stays replaceable, and imported without card
images — see [ADR 0004](adr/0004-the-canonical-card-catalog-source.md). Nothing
in `packages/domain` names that source: `Card` and `Set` carry facts, and a
provider key reaches them only as a `CardExternalId`. Each import's provenance
lands in the immutable `card_database_version` record; there is no competing
provenance table.

The import itself is an adapter and nothing on the request path may reach it.
`tcg_api.catalog.tcgdex` is the only module in the service that binds to an HTTP
client, `tcg_api.catalog.snapshot` is the provider-neutral format and write path
it produces, and `services/api/tests/test_import_purity.py` asserts that
importing the FastAPI app pulls in neither. Dropping the source costs a `DELETE`
from `card_external_ids` and the deletion of one module.

The market-price provider was a separate open decision and is now settled:
[ADR 0006](adr/0006-the-v1-market-data-provider.md) selects PokePriceTracker
for V1, behind `MarketDataProvider` and nothing more, with manual curation
retained as the fallback.

What that decision costs is recorded in the database rather than in an ADR alone.
`market_providers` holds one row per provider carrying `license`,
`commercial_use` and `terms_reference` — enforcement fields, not documentation.
ADR 0006 relies on one right no shortlisted candidate grants expressly, and gates
commercial use on a subscription tier, so "what were we allowed to do with this
price" has to have an answer years from now without anyone remembering. The row
carries the provider's name as its own terms spell it, alongside the lowercase
slug every observation is stamped with; and it is append-only, because an
`UPDATE` to `commercial_use` would retroactively relicense every price already
gathered under the old terms.

### Everything is versioned and immutable

Grading rules, model bundles, dataset versions and market snapshots are
versioned and never overwritten. A historical analysis keeps the exact versions
it used, so it can be re-derived rather than re-guessed. Models are referenced by
explicit version — `grading-psa-v0.2.0` — never by `/latest/`.

Every analysis records: application version, model bundle version, card database
version, grading rules version, market snapshot ID, economic configuration, and
the input image hashes.

The card database version was the first of those seven to become a real table.
`card_database_versions` holds one row per import run — identifier, source,
licence, upstream revision, when the data was made, and how much of it there was
— and `GET /catalog/version` reports the current one. Immutability there is a
database guarantee rather than a convention: a trigger refuses `UPDATE` and
`DELETE`, so a re-import publishes a new version instead of editing an old one,
and the identifier an analysis recorded still finds what it recorded.

`market_observations` is where prices land, one row per price a provider
reported for a card at a moment, and it is append-only for the same reason: a
corrected price is a new observation rather than an edit, which is what makes
price history and §36's snapshots honest. Two of its columns are worth knowing
about. The grade is a **text key** — BGS issues a 9.5 where PSA and TAG do not,
all three issue half grades elsewhere, and §24's collapsed tails (`7_or_lower`)
are legal keys — so a numeric column could not hold what the domain produces.
And `market_type` is **generated from the grading company rather than written**,
so a row claiming to be a raw price while carrying a grading company is not
representable at all; the rule is stated once, in `PriceObservation`, and this is
that same statement in SQL.

**A market snapshot is where that append-only guarantee pays for itself.** Spec
§36 requires every analysis to use one, so a result from last month can be
re-derived with the prices it actually used. `market_snapshots` holds four
things — which provider, when it was cut, the identifier of the ingestion run
behind it, and nothing else. In particular it holds **no list of observations**,
which §36's own diagram invites: the set is already determined, because
`market_observations` never changes and `created_at` records when a row *landed*
rather than when the price was seen. So a snapshot comprises "this provider's
prices stored at or before this moment", and re-resolving it a year later
returns the same figures.

The cut is on when a row was stored, and that is the load-bearing half. A
backfilled price is seen long before it is stored, so a snapshot cut on when a
price was *seen* could be joined retroactively by a late arrival — and a
snapshot that resolves differently on two readings of the same data is not
immutable at all.

The alternative, stamping each ingested row with the snapshot it belongs to, is
worse than redundant: a daily run may not reach every one of 49,399 cards, so a
snapshot built from what that run wrote would hold the day's *coverage* rather
than the market as of that day, and every card the run missed would report no
price at all while a perfectly good one from yesterday sat on file. A snapshot
resolves the latest **known** price, not the latest fetched one.

`data_version` is generated from the cut rather than written, because no market
provider surveyed publishes a version of its own — so the identifier is the
ingestion date, and a run that could name its own version could name one that
disagreed with when it was cut. It is also the date stamp the results UI has to
show beside a price: a historical analysis reports an old snapshot by design,
and a record of a past date is not a stale price presented as a current one.

The grading rules version is the second. `grading_rules` holds one row per
published standard per company — identifier, when it took effect, where it was
read and when — under the same guarantee and the same trigger. A correction is a
new version with a new effective date, never an edit.

Its one departure is worth knowing: **the date a standard stopped applying is
derived rather than stored.** A version is in force from its effective date until
the next version of the same company begins, so a company's ranges cannot overlap
at all rather than being rejected when they do — and there is no second write to
close a range, which is what lets the immutability guarantee stay unconditional.
Every record a caller receives still carries the end date; the resolver computes
it. What is stored instead is the honest absence: PSA publishes an effective date
and TAG and BGS publish none, so those two carry no date rather than one inferred
from a copyright footer.

The rules body itself is empty, by decision. Each company's grading standard is
that company's copyrighted text and this repository does not reproduce it; what
reproducibility needs is the identifier, plus a source a human can open.

`analyses` carries the rest of the record: `model_bundle_version`,
`market_snapshot_id`, `economic_configuration_id`, `application_version`,
`card_database_version` and `grading_rules_version`. `application_version` is on
the analysis as well as on the session, and the duplication is the point — a
session lives for days, so the version that opened one is not necessarily the
version that ran this. Every value is resolved when the analysis ran, never a
pointer to whatever is current: the worker captures them at the moment it claims
the analysis, which is the one moment at which "what is this being computed
against" has an answer. `market_snapshot_id` is the second of the six the worker
actually resolves, and the only one carrying a foreign key — `RESTRICT`, so
pruning can never make a recorded analysis unresolvable, where a catalog version
is an identifier worth keeping even if its record went. Four of the six are
still null through V1: no model bundle or economic configuration exists yet;
nothing has ingested a price, so there is no snapshot to name and the run
records that rather than inventing one; and grading rules now exist but nothing
in an analysis consults them until per-company grade prediction arrives — so
recording a rules version on a run that never applied one would be a false
claim. A documented absence rather than a gap.

Immutability is a database guarantee here too. A `BEFORE UPDATE` trigger refuses
to change any of those six once it holds a value, so a re-run is a new analysis
rather than an edit. It guards `UPDATE` only, and deliberately not `DELETE`: an
analysis expires with its session, and a trigger that made a recorded analysis
undeletable would make expiry impossible. `GET /analyses/{id}` reports the whole
record — including §57's input image hashes, which are `images.sha256` — read
from the row rather than resolved when the request arrives.

### Analyses expire by default

V1 has no accounts. An anonymous session is the whole of a user's continuity, and
the schema is a spine: `analysis_sessions` → `analyses` → `images`, each child
owned by its parent through `ON DELETE CASCADE`.

Uploaded photographs may contain the user's hands, home and surroundings, so
expiry is the default and retention the exception that needs a justification.
`analysis_sessions.expires_at` is `NOT NULL` from the first migration and has no
column default — the retention period is a policy, and a policy hidden in a
schema default is one nobody reviews. Deleting an expired session is then a
single statement that reaches every analysis and every image beneath it.

One thing the cascade does not do: **deleting the row is not deleting the
object.** The retention job has to read `original_uri` and `normalized_uri` and
remove the stored files, verified against storage rather than against the
database. A sweep that only deletes rows leaves every expired photograph in
object storage forever, which is the failure the policy exists to prevent,
reached through the mechanism meant to prevent it.

The sweep that does this runs hourly inside the analysis worker, and deletes the
objects before the rows for exactly that reason.
[`retention.md`](retention.md) is the written policy — what is kept, for how
long, what is deliberately not covered, and how to run and verify a sweep.

The link into shared reference data points the other way and is `RESTRICT`:
`analyses.card_id` is nullable until the user confirms an identification, and a
catalog re-import can never delete a card an analysis names. The confirmation
itself is `POST /analyses/{id}/confirm-card`, and the identifier it takes is
resolved against the catalog before it is written — spec §55 says never to trust
client-side card metadata, and the foreign key is the backstop rather than the
check.

### Uploaded images are untrusted input

The upload endpoint is the product's primary attack surface, so what it refuses
matters more than what it accepts. Four rules, and each defends against
something the others do not.

The **type is sniffed, not declared**. `POST /analyses/{id}/images` takes the
image as the raw request body and is never given a filename — there is no
parameter through which one could arrive — so "sanitize filenames" and "generate
server-side storage paths" (spec §55) are properties of the endpoint's shape
rather than of a validator somebody has to keep correct. What is recorded as the
image's type is what a decoder read out of its bytes.

The **byte limit and the pixel limit are separate**. A byte limit bounds what
crosses the network; it is no defence at all against a two-kilobyte PNG that
declares sixty thousand pixels square and costs eleven gigabytes to decode. The
dimensions are read from the file's header and compared *before* anything is
decoded, and the decode is what proves the file is an image at all rather than a
header that parses.

**Personal metadata does not survive, and no pixel changes.** Spec §54 notes
these photographs may contain hands, backgrounds and personal surroundings; EXIF
GPS is worse, because it is exact. It is removed before anything is stored — but
by rebuilding the JPEG from its own marker segments with the entropy-coded scan
copied byte for byte, not by re-saving through a decoder. A re-encode would
degrade precisely the fine surface and edge signal the condition models exist to
measure.

The **digest is over the bytes that were stored**, not the bytes that arrived,
so the content-hash preprocessing cache keys on something that exists and the
retention sweep can verify what it deleted. A retake replaces its predecessor
and the superseded object is deleted, because an object no row names is one a
sweep working from rows will never find.

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

Which sources qualify was a separate open decision and is now settled:
[ADR 0008](adr/0008-permitted-training-image-sources.md) approves photographs
this project takes of cards it owns, photographs contributed under a written
grant, and — once a consent mechanism exists — this product's own user uploads.
Every third-party corpus is rejected, and the evidence for each is in
[`training-image-provenance-research.md`](training-image-provenance-research.md).
Two rights layers are determined separately there, because neither answers the
other: the photographer's copyright in the photograph, and the depicted card's
own artwork. The second is granted by nobody, which is why no dataset produced
here is ever published — `redistribution_allowed` is `false` even on an image
this project took itself.

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
ml/             card-detection, card-identification, image-quality,
                normalization, centering, corners, edges, surface, condition,
                grading/{psa,tag,bgs}, evaluation
packages/       domain, grading-companies, market-data, economic-engine, shared
database/       migrations, seeds, fixtures
datasets/       schemas, manifests, documentation
infrastructure/ docker, local, deployment
```

These are **logical boundaries, not microservices**. V1 deploys as one API, one
worker and one web application; the tree exists so the seams are in the right
places when something eventually has to move, not so that everything is
separately deployable now. The worker is a *process* boundary rather than a
codebase one: it runs the same application with a different command, so the code
that runs a job cannot drift from the code that enqueued it. Its isolation
(spec §56) is the container's — no published port, every capability dropped.

It has its own image since the image-quality gate (#36), and the two differ by
exactly one uv extra — which since card boundary detection (#37) and
normalization (#38) carries three ml packages rather than one. All three bring
OpenCV; a CV stack decoding untrusted photographs does not belong in the
container answering HTTP. What holds that split up is a lazy import — the API
imports `tcg_api.analysis.jobs` merely to enqueue, so the wiring for all three
stages is imported inside the function that runs a job, and a purity test
asserts that importing the application reaches neither `cv2` nor any of them.

`ml/normalization` is not one of spec §7's names. §18 makes perspective
correction and normalization a stage of its own, and it got a package of its own
on the rule the two siblings above are shaped by: one stage, one package, and no
dependency between siblings — the card's quadrilateral crosses from the detector
to the normalizer as a domain type, so neither imports the other.

The split by language is not decorative either. `apps/*` is a pnpm/TypeScript
workspace; `packages/*`, `services/*` and `ml/*` are a uv/Python workspace,
because the domain invariants are imported by Python callers and must exist
exactly once. The frontend obtains its API types by generating them from the
FastAPI OpenAPI schema — see
[ADR 0001](adr/0001-language-boundaries-in-the-monorepo.md).

The endpoints that create work — starting an analysis, adding a photograph to
one, confirming its card, running it — are rate-limited per client address (spec §55, which names
analysis endpoints *and* image uploads),
counted in that same Redis so one limit holds across replicas. A throttled
request is a 429 with `Retry-After` and sits outside the spec §66 envelope, as
the 404 and the 409 already do — see
[ADR 0005](adr/0005-rate-limiting-the-analysis-endpoints.md). Polling is not
limited: spec §65 requires a client to do it.

Background processing is required rather than optional: ML inference is
long-running and must never block an HTTP request. `POST /analyses/{id}/run`
enqueues the work onto Celery over Redis and answers `queued` immediately; the
client polls `GET /analyses/{id}` for one of spec §65's nine states. `queued` is
not one of them — it is an acknowledgement, and no analysis row ever holds it.

A run rests at `awaiting_confirmation`: spec §20 forbids acting on an
identification the user has not confirmed, and nothing in the decomposed
milestones produces a candidate, so the user names the card themselves. Recording
that confirmation is what moves the analysis to `analyzing`, where it rests in
turn until the condition stages exist. Neither resting point is a stub standing
in for a result — an analysis reported as `completed` with every result column
NULL is exactly the confidently-wrong output the specification forbids.

Delivery is deliberately at-least-once, and safe because a run *claims* its
analysis with a single conditional `UPDATE` naming the states the move is legal
from. That one statement refuses an illegal transition, makes a duplicate
delivery a no-op and settles a race between two workers, which is why the state
machine is enforced in the database rather than checked in Python beforehand.
The legal moves themselves live in `packages/domain`, since the API, the worker
and the schema all have to agree about them.

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
