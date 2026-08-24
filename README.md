# tcg-analyzer

TCG Grading Advisor — photograph an ungraded Pokémon card, front and back, and
get back its identity, its physical condition, a probability distribution over
grades for each grading company, market values, the economics of grading it, and
a recommendation on whether grading is worthwhile.

## What this is, and what it is not

**This is not an official grading service.** It does not authenticate cards, and
it does not detect counterfeits. Its output is a probability distribution over
grades — never a guaranteed grade, and never a promise about what a grading
company will decide.

What it does is answer a question the grading companies do not: *given this
card, in this condition, at today's prices, is paying to have it graded worth
it?* Two independent halves produce that answer. The models predict the physical
outcome; the economic engine decides whether that outcome is worth pursuing.
Neither depends on the other's internals, and that separation is the single most
load-bearing rule in the codebase.

## V1 scope

| | |
| --- | --- |
| **Cards** | Pokémon, English and Japanese |
| **Grading companies** | PSA, TAG, BGS |
| **Input** | Ordinary front and back photographs, from a mobile camera or a desktop |
| **Analysis** | Identification, centering, corners, edges, surface, manufacturing defects, condition confidence |
| **Output** | A grade probability distribution per company, market values, grading economics, a recommendation |
| **Economics** | SGD, with configurable grading, shipping, insurance, miscellaneous and selling costs |
| **Sessions** | Anonymous. Mobile-first, desktop-compatible |

Deliberately **not** in V1: authentication, user accounts, collections,
portfolio management, social features, counterfeit detection, slab analysis,
crack-and-resubmit analysis, guided photography, grading submission, card
selling, CGC, ARS, other TCGs, currencies other than SGD, and monetization.

They are excluded from the scope, not from the design: the architecture has to
accommodate every one of them later without a rewrite.

## Architecture

Photographs become a neutral condition representation, that representation feeds
a separate model per grading company, and the resulting distributions meet a
pre-ingested market snapshot in the economic engine. Uncertainty is a valid
answer at every step — `insufficient_information` is a legitimate result, and
the pipeline stops rather than guessing when an image or an identification
cannot support the next stage.

The invariants that shape all of this — distributions rather than point grades,
replaceable providers, immutable versioning, provenance-gated training data —
are documented in **[`docs/architecture.md`](docs/architecture.md)**. Read it
before making a structural change; the decisions behind it are recorded in
[`docs/adr/`](docs/adr).

## Repository layout

| Path | Contents |
| --- | --- |
| `apps/` | TypeScript applications — `web` (Next.js) and `annotation` |
| `services/` | Python services — `api`, `analysis`, `market-data`, `ingestion` |
| `ml/` | Python ML modules — detection, condition, per-company grading, evaluation |
| `packages/` | Shared Python libraries — domain, ports, economic engine |
| `database/` | Migrations, seeds and fixtures |
| `datasets/` | Dataset schemas, manifests and documentation — **never images** |
| `infrastructure/` | Docker, local development, deployment |
| `docs/` | Documentation and architecture decision records |
| `tests/` | Repository-level tests |

These are logical boundaries. They are not microservices in V1.

Every directory carries a `README.md` describing its responsibility and the
milestone that fills it.

## Toolchain

Two workspaces, split by language — see
[ADR 0001](docs/adr/0001-language-boundaries-in-the-monorepo.md).

| Workspace | Manager | Members |
| --- | --- | --- |
| TypeScript | pnpm (`pnpm-workspace.yaml`) | `apps/*` |
| Python | uv (`pyproject.toml`) | `packages/*`, `services/*`, `ml/*` |

### Prerequisites

- **Node** 20+ with pnpm. pnpm is pinned by the `packageManager` field and
  provisioned by Corepack, which Node no longer bundles: install it once with
  `npm install --global corepack`, then `corepack enable pnpm`. Prefix commands
  with `corepack` (`corepack pnpm install`) if enabling shims needs
  administrator rights.
- **[uv](https://docs.astral.sh/uv/)**. The Python version is pinned in
  `.python-version`; uv will fetch it.
- **Docker**, with Compose v2.22+ — only for running the stack, not for the
  tests. `docker compose version` reports both.

### Run the whole thing

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait
```

That is the entire setup from a fresh clone. It starts PostgreSQL, MinIO and
Redis, runs the migrations, then starts the API, the analysis worker and the web
application in dependency order.

| | |
| --- | --- |
| Web application | <http://localhost:3000> |
| API | <http://localhost:8000> — `/health`, `/readiness`, `/docs` |
| MinIO console | <http://localhost:9001> |

The landing page reports whether it can reach the API, so **"Analysis API
reachable"** on <http://localhost:3000> means the whole stack is talking to
itself.

Swap `up` for `watch` to get hot reload — source changes are synced into the
running containers. Stop with `down`, or `down -v` to discard the database and
the bucket as well. See [`infrastructure/local`](infrastructure/local) for the
full reference.

The sections below are the host-based workflows. They remain the faster loop
for a focused change, and they are what CI runs.

### Commands

```bash
cp .env.example .env    # local configuration; `.env` is never committed
pnpm install            # resolve the TypeScript workspace
uv sync --all-packages  # resolve all Python workspace members
uv run pytest           # repository-level and per-package tests
```

### Configuration

Every variable the stack reads is documented in [`.env.example`](.env.example)
with a placeholder value. Copy it to `.env` and edit that — `.env` is
gitignored, and no credential is ever committed (the project may be
open-sourced). `tests/test_environment_example.py` fails if a setting exists in
code but not in the example, so the two cannot drift.

Configuration fails fast on a value that is wrong, and tolerates one that is
absent. A malformed `TCG_API_DATABASE_URL` — an unparseable URL, or a
synchronous driver where the engine is async — stops startup with a message
naming the variable. An *unset* one does not: the API starts, `/health`
answers, and `/readiness` reports `database: unavailable`, which is what a
fresh clone looks like before PostgreSQL is running.

The API's settings live in `services/api/src/tcg_api/config.py`; the web app's
in `apps/web/lib/env.ts`. Nothing else reads the environment directly, because
a variable only one module knows about is a variable that never reaches
`.env.example`.

#### Checks

CI runs exactly these, so a green run locally means a green run there. Nothing
in the pipeline is a step you cannot reproduce yourself.

```bash
uv run ruff check .                     # lint
uv run ruff format --check .            # formatting
uv run mypy packages/domain/src packages/shared/src packages/grading-companies/src packages/market-data/src services/api/src ml/image-quality/src ml/card-detection/src ml/normalization/src
uv run pytest -m "not integration and not object_storage"   # both need Docker

pnpm --filter @tcg/web lint
pnpm --filter @tcg/web format:check
pnpm --filter @tcg/web gen:api-types:check   # frontend types match the schema
pnpm --filter @tcg/web typecheck
pnpm --filter @tcg/web test
```

If `gen:api-types:check` fails, the API's OpenAPI schema and the committed
frontend types have diverged. Regenerate and commit:

```bash
pnpm --filter @tcg/web gen:api-types
```

#### Web

```bash
pnpm install
pnpm --filter @tcg/web dev     # http://localhost:3000
pnpm --filter @tcg/web test
pnpm --filter @tcg/web build
```

The app reads `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`),
validated by `lib/env.ts`. Next reads env files from the app directory, so for
`pnpm dev` copy `apps/web/.env.example` to `apps/web/.env.local`; the root
`.env` is what the Compose stack passes to the container.

#### API

```bash
uv run uvicorn tcg_api.main:app --reload   # API on http://localhost:8000
```

`GET /health` reports the service status and the application version,
`GET /catalog/version` reports which card catalog the deployment is serving,
`GET /cards/search` finds cards in it, and
`GET /cards/{id}` returns the canonical detail for one card — its name, set, card
number, language, rarity, variant and the external database identifiers recorded
for it. An identifier naming no card answers 404 under the spec §66 taxonomy; it
carries no prices and no card images.

`GET /cards/search` filters on `text`, `game`, `language`, `set_code`,
`card_number` and `variant`, all optional and ANDed, and pages with `limit` and
`offset`. `text` matches a fragment of the printed name without regard to case
and works for Japanese; `card_number` matches as a prefix, so `25`, `025` and
`025/165` all find the card printed `025/165`. Results are ordered by
`(set_code, card_number, variant, id)` — a total order, so paging neither drops
nor duplicates a row. Nothing matching is an empty page, never a 404.

`GET /grading-companies` lists the grading companies the product supports, each
with the exact grades it can issue and the version of its published standard in
force today (spec §23), so a result can be tied back to it. **Render the scale
from what this endpoint returns rather than hard-coding one**: PSA and TAG issue
eighteen grades and no 9.5, BGS issues nineteen and has one, and all three issue
half grades everywhere else — a picker built on a single shared scale misrenders
one of them, and refuses a PSA 8.5. A company added after V1 appears here with no
frontend change at all. The response is slow-moving reference data and says so
with `Cache-Control: public, max-age=3600`. It carries no grading fees: spec §45
makes those configurable economic inputs, so they belong to the economic engine's
configuration rather than to a table here that would go stale quarterly.

`POST /analyses` starts an analysis. There is no login and no registration:
V1 identifies a user by an anonymous session token only (spec §53), returned in
an HTTP-only cookie that every later call must carry. `GET /analyses/{id}`
reports the state of one, but only to the session that started it — an unknown
identifier, another session's analysis, a missing cookie and an expired one all
answer 404 with the same body, so the endpoint cannot be used to discover which
analyses exist. Sessions expire after `TCG_API_SESSION_TTL_SECONDS`; nothing
about the caller is recorded.

`POST /analyses/{id}/images?side=front` uploads one photograph, as the raw
bytes of the request body rather than as a multipart form. That is deliberate:
**no client filename ever arrives**, so spec §55's "sanitize filenames" and
"generate server-side storage paths" are satisfied by construction rather than
by validation, and the byte limit can be applied while the upload is still
arriving. Uploaded images are untrusted input (spec §56), so the file is
accepted only if its **content** — never the type it declares — is a JPEG or a
PNG, if it is within `TCG_API_UPLOAD_MAX_BYTES` and `TCG_API_UPLOAD_MAX_PIXELS`,
and if it decodes. The pixel limit is separate from the byte limit because a
two-kilobyte PNG can declare a bitmap of eleven gigabytes, and it is checked
from the file's header before anything is decoded. EXIF — including GPS — is
removed before the image is stored (spec §54), losslessly: a JPEG is rebuilt
from its own marker segments with the scan copied byte for byte, so the fine
surface detail the condition models read is never re-compressed. The digest
recorded on the row is of the bytes that were stored, not of the bytes that
arrived. A second upload for the same side replaces the first, and the object it
replaced is deleted. The analysis reaches `uploading` on the first photograph
and `uploaded` once both sides have arrived, which is the one state
`POST /analyses/{id}/run` may start from. Every rejection is `invalid_image`
with a message naming the rule and nothing about the decoder.

`POST /analyses/{id}/confirm-card` records which card the user is holding
(spec §20). The card identifier arrives from a client and is therefore not
trusted (spec §55): it is resolved against the catalog before it is written, and
one naming no card is refused with the same `card_not_identified` that
`GET /cards/{id}` answers with. Only an analysis in `awaiting_confirmation` may
take a confirmation, and recording one moves it to `analyzing`. Spec §65's
states move forwards only, so there is no second confirmation and no changing
the card afterwards — both are a 409, and a card chosen in error is corrected by
starting a new analysis.

The four writes — `POST /analyses`, `POST /analyses/{id}/images`,
`POST /analyses/{id}/confirm-card` and
`POST /analyses/{id}/run` — are rate-limited
per client address (spec §55, which names analysis endpoints *and* image
uploads), `TCG_API_RATE_LIMIT_REQUESTS` per
`TCG_API_RATE_LIMIT_WINDOW_SECONDS`, counted in the same Redis the job queue
runs on so the limit holds across replicas. A throttled request is a 429
carrying `Retry-After`, deliberately outside the spec §66 error envelope —
see [ADR 0005](docs/adr/0005-rate-limiting-the-analysis-endpoints.md). Polling
`GET /analyses/{id}` is not limited, and neither are the catalog reads. With
`TCG_API_REDIS_URL` unset, or Redis unreachable, the limiter lets requests
through rather than refusing them. The OpenAPI schema is at `/openapi.json`
and the interactive documentation at `/docs`. Settings are read from `TCG_API_`-prefixed environment variables or
from `.env` — see [Configuration](#configuration).

The image is built from the repository root, because `services/api` is a member
of the uv workspace and cannot be resolved without it:

```bash
docker build -f infrastructure/docker/api.Dockerfile -t tcg-api:dev .
docker run --rm -p 8000:8000 tcg-api:dev
```

The same image is what the local stack runs, both as the `api` service and as
the one-shot `migrate` service that applies the migrations before the API is
allowed to start.

#### Database

Every schema change arrives through a reviewed, versioned Alembic migration —
never ad-hoc DDL. Start PostgreSQL, then migrate:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait

export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg

uv run alembic upgrade head              # migrate up
uv run alembic downgrade -1              # migrate down one revision
uv run alembic revision -m "description" # new revision
uv run alembic current                   # which revision is applied
uv run alembic history                   # the revision graph
```

Tear the database down with `docker compose -f
infrastructure/local/docker-compose.yml down -v`. The `-v` discards the volume,
so the next `up` starts from an empty database and `upgrade head` rebuilds the
schema from the migrations alone.

`TCG_API_DATABASE_URL` is a SQLAlchemy URL using the asyncpg driver:

```text
postgresql+asyncpg://<user>:<password>@<host>:<port>/<database>
```

It is the single source of the connection string — the API service and Alembic
both read it, and it never appears in `alembic.ini`. Alembic reads the
environment only, so export it as above even when `.env` already carries it for
the API. The value in `.env.example` matches the Compose defaults: local-only
values, not secrets; see `infrastructure/local/README.md`. A real credential
belongs in `.env`, which is not committed.

Migrations are portable, plain PostgreSQL. Nothing may depend on a
Supabase-specific feature (spec §8), and no extension is required.

The schema's source of truth is the `MetaData` in
`services/api/src/tcg_api/tables.py`, which `database/migrations/env.py` reads as
`target_metadata`. The tables themselves are declared per domain — the card
catalog in `tcg_api/catalog/tables.py`, the analysis spine in
`tcg_api/analysis/tables.py` — and `tcg_api/table_registry.py` imports them all,
which is what makes that `MetaData` complete. `env.py` reads it from the registry
for exactly that reason. Declare a new table in one of those modules as well as in
its migration, and register a new domain in the registry, or
`alembic revision --autogenerate` will propose dropping it.

Once the schema is up, load the hand-authored card catalog fixtures:

```bash
uv run tcg-seed-catalog
```

Roughly twenty English and Japanese cards under a `manual` provider, enough to
search, identify and price against. It is idempotent, so re-run it after editing
a fixture; see `database/seeds/README.md`. These fixtures are the catalog a
developer gets without a network, and
[ADR 0004](docs/adr/0004-the-canonical-card-catalog-source.md) keeps them as the
floor if the TCGdex position ever has to be withdrawn.

For a real catalog, import one from TCGdex:

```bash
uv run tcg-import-catalog --version pokemon-catalog-tcgdex-v0.1.0 --language en --language ja
```

Two phases in one command. It fetches the source into a *snapshot* — three
JSON files under `.catalog-snapshots/tcgdex`, gitignored — and then loads that
snapshot into the database in a single transaction. The split is not
decoration: rarity and printing variants come only from TCGdex's per-card
endpoint, so a full English-and-Japanese import is roughly 36,000 requests. A
snapshot is a reviewable artifact, it carries a `sha256` digest that a later
load verifies, and it can be replayed exactly.

```bash
uv run tcg-import-catalog --version pokemon-catalog-tcgdex-v0.1.0 --language en --set base1
uv run tcg-import-catalog --from-snapshot .catalog-snapshots/tcgdex
```

The first narrows the run to one set, which is how to check a change in seconds
rather than an hour. The second loads an existing snapshot and uses no network
at all. `--cache-dir` keeps raw card payloads so an interrupted full run resumes
instead of starting over, and `--fetch-only` writes the snapshot without
touching a database. A TCGdex set id belongs to one language — `base1` is
English, `SV2a` is Japanese — so `--set` imports from whichever `--language` has
it, and a set found in none of them is an error rather than a silent no-op.

`--version` is required and is never reused. Two imports are two versions: the
rows they write converge, the records of the runs accumulate.

The catalog is versioned. Every run that writes it — the seed loader and the
import above — publishes an immutable `card_database_version` recording the
identifier, the source, the licence relied upon, the upstream revision and the
record counts. That is one of the seven fields spec §57 requires an analysis to
keep so it can be re-derived rather than re-guessed, and `GET /catalog/version`
is how a client reads it. No card images are imported: TCGdex's MIT licence
covers its compilation, not The Pokémon Company's artwork.

Published versions are never rewritten: a database trigger refuses `UPDATE` and
`DELETE` outright, and a re-import publishes a new version rather than editing an
old one. Identifiers are explicit and ordered — `pokemon-catalog-v0.3.0`, never
`/latest/`.

Tests that need a live database are marked `integration` and skip when
`TCG_API_DATABASE_URL` is unset, so the default suite never needs Docker:

```bash
uv run pytest -m integration   # requires PostgreSQL to be running
uv run pytest -m "not integration"
```

The catalog import is tested against recorded payloads, so it needs no network
either. One test does reach `api.tcgdex.net`, to notice when the source changes
shape; it is marked `network`, deselected in CI, and run by hand after changing
`services/api/src/tcg_api/catalog/tcgdex.py`:

```bash
uv run pytest -m network
```

#### Object storage

Uploaded card images live in S3-compatible object storage — MinIO locally, any
S3-compatible provider in a deployment. The application never talks to either
directly: it goes through the `ObjectStorage` port in `packages/shared`, so the
provider is replaceable ([ADR 0002](docs/adr/0002-object-storage-behind-a-port.md)).

The same `up` that starts PostgreSQL starts MinIO and creates the bucket:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait

export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
```

The browser console is on <http://localhost:9001>. `down -v` discards the
`minio-data` volume along with the database's, so the next `up` starts from an
empty bucket.

Two rules matter more than the configuration:

- **Storage keys are generated server-side, always.** `generate_key` takes no
  filename argument, so a client-supplied name cannot reach a storage path
  (spec §55). An original filename may be kept as metadata via
  `sanitise_filename`, and never as a path.
- **Signed URLs are short-lived and scoped to one object.** A signed URL is a
  bearer credential nobody can revoke, so the only bound on its misuse is
  `TCG_API_STORAGE_SIGNED_URL_TTL_SECONDS`.

Tests that need a live MinIO are marked `object_storage` and skip when
`TCG_API_STORAGE_ENDPOINT_URL` is unset. They are separate from `integration`
because the two need different services:

```bash
uv run pytest -m object_storage   # requires MinIO to be running
```

#### Background jobs

Analysis is asynchronous: `POST /analyses/{id}/run` hands the work to a Celery
worker over Redis and answers `queued` at once, because inference takes far
longer than an HTTP request should (spec §8). Progress is polled through
`GET /analyses/{id}`, which reports one of spec §65's nine states — `queued` is
an acknowledgement rather than a state, and no analysis ever holds it.

The same `up` starts Redis and the worker. The worker runs the same application
with a different command, from an image of its own — the API's plus the `worker`
extra, which brings the image-quality gate, the card detector and perspective
normalization, and with them OpenCV:

```bash
docker compose -f infrastructure/local/docker-compose.yml logs -f worker
```

Five properties are load-bearing rather than incidental, and each has a test:

- **The API image does not contain OpenCV.** The worker decodes untrusted
  photographs; the API answers HTTP. `tcg_api.analysis.jobs` imports the
  pipeline's wiring inside the function that runs a job rather than at module
  scope, because the API imports that module to enqueue — moving it to the top
  of the file is a tidy-up that stops the API container from starting.

- **The worker accepts JSON and nothing else.** A Celery worker willing to
  deserialize pickle from a broker an attacker can write to is arbitrary code
  execution. `task_serializer`, `result_serializer` and `accept_content` are all
  pinned, and none of them may ever gain `pickle`.
- **The broker is authenticated, even locally.** There is no default
  `TCG_API_REDIS_URL`; a deployment sets `rediss://` with credentials.
- **A repeat delivery is a no-op.** Delivery is at-least-once, and a run claims
  its analysis with a conditional `UPDATE` whose `WHERE` clause names the states
  the move is legal from — so a duplicate job finds nothing to do, and two
  workers cannot both claim one analysis.
- **A dead-lettered job records the job id, the error and the attempt count —
  and nothing else.** Analysis payloads reference photographs of somebody's card
  and their surroundings; keeping one indefinitely so a job nobody re-drives
  could be re-driven is not a trade this project makes (spec §54).

#### The image-quality gate

Spec §18 puts a quality gate between file validation and card detection, and
spec §19 fixes what it may conclude: `unusable` stops the analysis, `poor`
continues but the user must be told. The M2 implementation is OpenCV heuristics
in `ml/image-quality`, and M7 replaces it with a model behind the same
signature.

Six of §19's eleven conditions are measured from the pixels alone — blur, low
resolution, glare, poor exposure, excessive darkness, excessive brightness. The
other five all reduce to "where is the card", and are answered from the card
boundary the detector below supplies. Without one they are reported
**undetermined with a reason** rather than guessed, and a photograph with five
conditions unchecked cannot be `good` however sharp it is: the best available
verdict is `acceptable`, which here means "nothing wrong found, and something
not looked at".

Every verdict is persisted on `images` — the status, a `[0, 1]` score and all
eleven findings — and served by `GET /analyses/{id}`, which is what lets
`/analyze` say what was wrong before it hands off to the catalog. The thresholds
that produced a verdict are recorded beside it, along with the versions of the
gate and of the detector, so a later model can be compared against the baseline
that actually ran.

#### Card boundary detection

`ml/card-detection` locates the card so that everything downstream operates on
the card rather than on the table it is lying on (spec §18). It takes the stored
bytes and returns four corners **clockwise from the top left**, in the original
photograph's coordinates, with a detection confidence — or an explicit "no card
found", never a guessed quadrilateral. The V1 implementation is an OpenCV
contour baseline; a learned detector is an M7 option behind the same signature.

Three things about it are deliberate and easy to undo by accident:

- **The corner order is validated, not documented.** Perspective correction
  reads the four corners positionally, so a wrong order does not fail — it
  silently rotates or mirrors the card. `CardGeometry` refuses a quadrilateral
  that does not run clockwise around a convex shape, so a mirrored one is not
  representable.
- **The boundary is not cropped tight.** M7's edge and corner analysis needs the
  card's actual edge, and a tight crop shaves the whitening that matters most.
- **Concentric quadrilaterals are one card.** A sleeve, a top-loader and the two
  walls of an edge ribbon all put a second quadrilateral around the first;
  counting those as two cards would refuse the photograph for `multiple_cards`.
  The spread between them is what answers sleeve obstruction instead — the
  weakest heuristic in the pipeline, and one that costs a `poor` rather than a
  refusal.

#### Perspective correction and normalization

`ml/normalization` warps the detected quadrilateral into the standardized
artifact every later stage reads — spec §18, and M2's acceptance criterion. It
is a **756 x 1056 PNG**: 12 pixels per millimetre of a 63 x 88 mm card, so the
output is exactly a real card's proportions with no rounding and a centering
ratio measured on it means what it says. The transform that produced it is
persisted alongside, because spec §51's post-V1 defect visualisation draws boxes
on the *original* photograph and that mapping is not recoverable afterwards.

Four things about it are deliberate:

- **Nothing is enhanced.** No sharpening, no denoising, no contrast stretching,
  no white balance. Every stage downstream exists to measure scratches,
  whitening and print lines; a denoised scratch is one the model cannot see and
  a sharpened edge is whitening that was never there.
- **The resampling is two steps.** `warpPerspective` has no area filter, so
  warping a 4000-pixel card straight down to 1056 point-samples it, and the
  moire that comes back is fabricated surface texture. The warp goes to an
  integer multiple of the output instead and one box filter takes it down.
- **The artifact is aspect-normalized, not upright.** The detector anchors its
  traversal at the corner nearest the frame origin, so a card photographed on
  its side is rotated a quarter turn to put its short edge first — which fixes
  the proportions. Which of two rotations puts the printed top at the top needs
  the artwork read, and that is card identification's question. The quarter turn
  applied is recorded.
- **No card located means no artifact.** `normalized_uri` stays NULL rather than
  holding a resized whole frame, which would be a standardized artifact of the
  table the card was lying on. The gate degrades the same way, capping such a
  photograph at `acceptable`. The original is always kept unmodified.

#### The reproducibility record

Spec §57 requires every analysis to record what it was computed against, so a
historical answer can be re-derived rather than re-guessed. Eight fields, and
`GET /analyses/{id}` reports them together under `reproducibility`:

| field | in V1 |
| --- | --- |
| `analysis_id` | the analysis's own `id` |
| `application_version` | the version of the service that ran it |
| `card_database_version` | the published catalog identifier that was current |
| `image_sha256` | a digest per side, of the bytes that were *stored* |
| `model_bundle_version` | null — no model exists yet |
| `grading_rules_version` | null — no grading rules exist yet |
| `market_snapshot_id` | null — market data is a later milestone |
| `economic_configuration_id` | null — the economic engine is a later milestone |

Three things about it are load-bearing:

- **The values are captured when the run claims the analysis**, not when the
  analysis is created and not when it is read. A record resolved at read time
  would describe whatever is current now, which is the one thing §57 forbids —
  and it is why `application_version` is on the analysis as well as on the
  session: a session lives for days, and a deployment can happen inside one.
- **They are explicit identifiers, never pointers.** `card_database_version`
  holds the identifier `GET /catalog/version` reports, resolved at that moment;
  "current" and `/latest/` are not values.
- **A written field cannot be changed.** A `BEFORE UPDATE` trigger refuses it,
  so a re-run is a new analysis rather than an edit. `UPDATE` only — an analysis
  still expires with its session, and guarding `DELETE` would make that
  impossible.

A null is a documented absence, not an omission. The four components that do not
exist yet have columns anyway, so that a null years from now reads as "there was
nothing to record" rather than as a field somebody forgot to write.

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the working conventions: one primary
capability per pull request, Conventional Commits, the PR description headings,
and the Definition of Done a change has to meet before it is finished.

Two of those rules are worth repeating here, because their cost is paid in the
history rather than in review: **never commit model weights, training images,
API keys or provider credentials**, and **do not skip hooks**. The project may
be open-sourced later, so proprietary assets have to stay out of the history,
not merely out of the working tree.

## Documentation

| | |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | Domain architecture, the analysis pipeline, the invariants |
| [`docs/retention.md`](docs/retention.md) | How long uploaded photographs are kept, and what deletes them |
| [`docs/adr/`](docs/adr) | Why things are the way they are, one decision per file |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Commits, pull requests, Definition of Done |
| [`.env.example`](.env.example) | Every variable the stack reads |
| `/docs` on the running API | The generated OpenAPI reference |

Each directory also carries its own `README.md` describing what belongs in it.
