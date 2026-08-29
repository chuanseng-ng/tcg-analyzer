# The database

PostgreSQL, reached through SQLAlchemy's asyncpg driver and migrated with
Alembic.

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
`tcg_api/analysis/tables.py`, the published grading standards in
`tcg_api/grading/tables.py`, the market data in `tcg_api/market/tables.py`, the
economic configuration in `tcg_api/economics/tables.py` and the dataset,
provenance, annotation and membership records in `tcg_api/datasets/tables.py` —
and `tcg_api/table_registry.py` imports them all,
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
[ADR 0004](adr/0004-the-canonical-card-catalog-source.md) keeps them as the
floor if the TCGdex position ever has to be withdrawn.

The published grading standards are seeded separately:

```bash
uv run tcg-seed-grading-rules
```

One row per published PSA, TAG and BGS standard (spec §23), written into
`grading_rules` from the versions `tcg_grading_companies` carries. It is
idempotent, and a published version is never rewritten regardless of what the
loader asks for — the database refuses the update. `GET /grading-companies`
reads its `rules` from this table and each grade scale from the package, so a
deployment that has not run this still serves all three scales and reports
`rules` as null.

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

## Training images

Training photographs enter the corpus through their own command, never through
the API — nothing in the dataset domain is a consumer surface. One invocation is
one physical card, because the front and back of one copy must group together
and never split across a train/test boundary:

```bash
uv run tcg-ingest-training-images --front front.jpg --back back.jpg --source first_party --acquisition-method photographed_before_submission --license "owned outright" --source-reference "PSA 12345678" --commercial-use-allowed --derivative-use-allowed --acquired-at 2026-08-01T10:00:00+08:00
```

It creates one `physical_copies` row and reports its identifier; pass that back
as `--physical-copy-id` to add a later session's photographs of the same card —
which is how a card's post-grading photographs join its pre-grading ones.
`--certification-company` and `--certification-number` go together and record a
slab already owned. `--card-id` is optional: a directory of photographs can be
ingested before anyone has identified what is in them.

**The two rights flags are deliberately not required, and omitting one is a
refusal.** `--commercial-use-allowed` and `--derivative-use-allowed` default to
*unstated*, and
[ADR 0008](adr/0008-permitted-training-image-sources.md) treats a null, an empty
string and an absent field as one answer: refusal. So does the database — the
gate is a `CHECK` rather than a convention — but the command refuses first, so
the message names the rule instead of a constraint. There is no
`--redistribution-allowed` flag: ADR 0008 makes it false on every approved
source, including photographs this project took itself, because the artwork in
them is not ours.

A source outside ADR 0008's four approved classes is refused by name. A
photograph already in the corpus is refused too — `sha256` is unique here, where
`images.sha256` in the analysis domain deliberately is not — and because the row
is written before the bytes are, that refusal stores nothing at all.

Validation is the analysis domain's, reused rather than reimplemented: the type
is sniffed instead of trusted, the byte and pixel limits apply separately, and
EXIF — GPS included — is stripped losslessly before storage, so the recorded
digest is over the bytes that were kept. A camera export larger than
`TCG_API_UPLOAD_MAX_BYTES` is refused; raise it for the run rather than
expecting a second limit to exist.

Ingestion stores the photograph and nothing else. `normalized_uri` and
`normalization_details` stay NULL until `tcg-normalize-training-images` has run —
`width` and `height` on this table are the **photograph's** and are NOT NULL,
which is why the artifact's size lives in the details rather than overloading
them the way `images` does.

## Normalizing a training image

Spec §30's annotation tool shows an image and §21's centering is measured on it,
and the annotation schema fixed what those measurements are *of*: fractions of
the standardized 756x1056 artifact, never pixels of a photograph. An annotator
marking a corner at 12% across a *photograph* has said nothing comparable about
the card, because the next photograph of it is framed differently. So the
artifact is an object with a key of its own, and this is what produces one:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
uv run tcg-normalize-training-images
```

**It runs from the worker image**, for the reason the deduplication pass does:
straightening a card needs card detection and normalization and therefore
OpenCV, which only `worker.Dockerfile` installs. That is also why this is a pass
rather than something a request does on demand — `tcg_api.main` may not reach the
CV stack at all, so normalizing while an annotator waits is unavailable rather
than merely slow, and `training_images.normalized_uri` is a column for exactly
that reason.

**A stored artifact is never replaced.** The pass selects rows whose
`normalized_uri` is NULL and nothing else, and there is deliberately no
`--force`. An annotation is a fraction of *the artifact its annotator saw*, so
re-warping an image somebody has judged would move every stored coordinate
without touching a row in `image_annotations`. A normalizer version bump is a
deliberate act with a re-annotation behind it — which is the one way this differs
from the fingerprints, where a version bump invalidates every row on purpose.

An image the detector finds no card in gets no artifact and is examined again on
the next run. That is not a failure: the annotation tool shows the photograph
and says which it is showing, because a coordinate cannot be taken against it.

## Detecting near-duplicate training images

Spec §28 puts deduplication between image validation and annotation, and §32
forbids splitting near-identical photographs of one card across train and test.
**The exact half of that is already unrepresentable**: `sha256` is unique on
`training_images`, so re-ingesting identical bytes is refused before anything is
stored. What is left is the near half — the same photograph resized,
recompressed, or the same card retaken under different light, each of which is a
different digest and the same card.

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
uv run tcg-detect-duplicate-training-images
```

It fingerprints every training image that has none, then reports the groups the
splitter must not break apart. **It runs from the worker image**, not the API
one: a fingerprint is taken over the standardized 756x1056 artifact, so producing
one needs card detection and normalization and therefore OpenCV, which only
`worker.Dockerfile` installs. Hashing the artifact rather than the photograph is
what takes framing and perspective out of the comparison for free.

Two hashes are stored per image, the second of the artifact turned 180 degrees.
Normalization puts the card's short edge first but makes no claim about which way
up it is *printed* — only reading the artwork could say — so an upside-down
retake would otherwise hash to something unrelated to its twin.

**Pairs and groups are not stored.** They are a pure function of the stored
hashes and a threshold, computed when asked, the same way a market snapshot
derives its membership from a cut-line rather than listing it. Re-running is
cheap: an image whose fingerprint is already current is skipped, and only a
detector or normalizer version bump invalidates one.

**The threshold is provisional and the command says so on every run.** Ten of 64
bits was chosen against synthetic fixtures, because no real card photographs
exist in this repository and none may be committed — ADR 0008 makes
`redistribution_allowed` false on every approved source. Measure it against your
own photographs and read the valley off the histogram:

```bash
uv run tcg-detect-duplicate-training-images --measure ~/cards
```

That mode reads a local directory, reports the distance distribution, and touches
neither the database nor object storage.

**A group is a finding for a person, not a verdict.** A perceptual hash cannot
tell two copies of one printing apart — the artwork is identical and condition
differences are sub-pixel — so a group spanning two `physical_copies` rows is
either one card entered twice under two submissions, or two genuine copies the
hash cannot separate. Over-grouping costs a little balance in the split;
under-grouping leaks, which is the failure §32 exists to prevent, so the pass
errs towards grouping and the report names the ambiguity rather than resolving
it.

## Publishing a dataset version

Spec §31 requires every training run to reference a `dataset_version` and forbids
a model referencing `/latest/`. This is what produces one:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
uv run tcg-publish-dataset-version --version pokemon-condition-v0.1.0 --seed 20260828
```

It splits the corpus as it stands, writes the version row, every member's split
and the seed **in one transaction**, and renders the manifest into
`datasets/manifests/`. Both tables refuse an `UPDATE` in a trigger, so a frozen
version cannot be edited; that a member cannot be *added* afterwards is held by
the members being written by the transaction that created the version and by
nothing else. A re-split is a new version, and a republished name is refused by
`uq_dataset_versions_version`.

**The seed is required and is not defaulted.** It is derivable from nothing, and
it is the only thing that makes the split re-derivable years later — which is why
`dataset_versions.split_seed` is a NOT NULL column and the achieved proportions
are not.

**The manifest is a render of the rows, never a record.** Counts, achieved
proportions and the per-source provenance mix are recomputed on every render:

```bash
uv run tcg-publish-dataset-version --version pokemon-condition-v0.1.0 --regenerate
```

That reads the database, writes nothing to it, and reproduces the file **byte for
byte** — which is also how a lost or truncated manifest is recovered. Nothing is
stamped at render time, no generated-at and no application version, because
either would make the first regeneration differ from the file it replaced.

**It runs from the API image**, unlike the deduplication pass: freezing a corpus
decodes no photograph, so nothing on this path needs OpenCV.

A manifest carries identifiers, content hashes, splits, storage keys and the
provenance pair — never bytes. ADR 0008 makes `redistribution_allowed` false on
every approved source, including the photographs this project took itself, so no
dataset is ever published; a list of identifiers and hashes carries no artwork and
is the most a version can leave behind. Each member carries enough for a training
run to resolve its file without reading the database, which is what ADR 0009
requires of `ml/*`.

## Annotating a training image

Spec §30's internal annotation application writes into two tables, and
[`datasets/schemas/annotation-schema.md`](../datasets/schemas/annotation-schema.md)
is where each of its eleven features lives. `image_annotations` holds §14's,
§15's and §16's defect markers; `centering_measurements` holds §21's two ratios.
They are two tables because a marker carries a label, a severity and a bounding
box and a measurement carries none of those — one table with a `kind` would leave
half of every row NULL by construction.

**Coordinates are fractions of the normalized 756x1056 artifact, never pixels of
the photograph.** An annotation stored against the artifact survives a retake and
compares across cards. Fractions rather than pixels of it, so `tables.py` never
imports `ml/normalization` — which would put OpenCV in the API image.

**Uncertainty is required on both tables.** `confidence` is NOT NULL with no
default, and every one of §14, §15 and §16's vocabularies carries `unknown`. An
annotator who cannot tell records that; a model trained on their confident guess
is worse than one trained on their admission.

Both tables are append-only: a corrected annotation is a new row, so a dataset
version that referenced the old reading keeps meaning what it meant. The
annotator is an opaque identifier under a grammar with no `@` in it, so spec
§53's restraint is enforced rather than requested.

The tool that writes these rows is `apps/annotation`, and it reaches them over
`/internal/annotation` — three reads today, and the writes when the annotation
controls land. That surface is **not** part of spec §64: it lives in this
application because §7 forbids an unnecessary microservice, and in the OpenAPI
schema because that is the only way `apps/annotation` can learn a shape
([ADR 0001](adr/0001-language-boundaries-in-the-monorepo.md)). What keeps it
internal is the `/internal` prefix, which is what an ingress rule matches.

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
