# Training and testing the models

This guide covers:
- how the models are trained and tested;
- what exists today and what does not;
- the loop that turns a photographed card into evidence;
- how much evidence a claim needs.

Each command's full rules are in [The database](database.md); this is the order
they run in and why.

## Where things stand

**Nothing is trained yet.** Every stage runs deterministic code whose version is
a constant in the source:

| Stage | What runs | Version |
| --- | --- | --- |
| Quality gate | Heuristic checks of spec §19's eleven conditions | `image-quality-heuristic-v0.5.0` |
| Card detection, normalization | OpenCV | `card-detection-opencv-v0.19.0`, `normalization-opencv-v0.2.0` |
| Condition | Four OpenCV analyzers, composed into one neutral reading | `centering-`, `corners-`, `edges-` and `surface-opencv-v0.1.0` |
| Grades | One declared baseline per company, confidence capped at 0.35 | `grading-psa-`, `grading-tag-` and `grading-bgs-heuristic-v0.1.0` |

The machinery around them is built:
- a dataset platform with a provenance gate, frozen versions and grouped
  splits;
- two evaluation harnesses;
- a model registry.

What is missing is **graded cards to learn from**: `grading_outcomes` holds no
rows. There is also **no training code**. The 0.35 cap is why every
recommendation today is `insufficient_information`; it sits below the 0.50
threshold (see [ADR 0011](adr/0011-the-v1-grade-predictor-basis.md)).

## The corpus, and the one rule about it

The training corpus is kept in its own database, **`tcg_corpus`**, in the same
PostgreSQL as the development database `tcg`.

- **Point corpus commands at `tcg_corpus` and pytest at `tcg`, and never swap
  them.** An integration test run truncates whatever database it is pointed at,
  so pytest refuses `tcg_corpus` outright (#196).
- **`down -v` destroys it.** It deletes the corpus, its artifacts and every
  annotation. It happened on 2026-09-07. What a committed manifest records
  (images, splits, annotations, centering, outcomes) comes back from the
  manifest plus the original photographs; follow
  [`rebuilding-the-corpus.md`](rebuilding-the-corpus.md). **Anything
  annotated since the last published version cannot be recovered.**

The current version is `pokemon-condition-v0.2.0`:
- 14 physical cards and 28 photographs, all photographed by this project before
  submission;
- split 20 / 4 / 4 across train, validation and test.

Its manifest is in [`datasets/manifests/`](../datasets/manifests).

To create the corpus database on a fresh stack:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
docker compose -f infrastructure/local/docker-compose.yml exec postgres createdb -U tcg tcg_corpus
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg_corpus
export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
uv run alembic upgrade head
```

The rest of this guide assumes those two variables point at the corpus. The
commands that decode photographs (normalization, deduplication and both
evaluations) need OpenCV. The host environment `uv sync --all-packages` builds
has it, and so does the worker image; the API image deliberately does not.

## The loop

```text
photograph a raw card you own ─► ingest (provenance gate) ─► normalize ─► find near duplicates
  ─► annotate its condition ─► submit it to PSA, TAG or BGS ─► record the grade that comes back
  ─► publish a dataset version ─► train (not built) ─► test ─► register ─► serve (not built)
```

### 1. Photograph and ingest

**Only four sources are approved**
([ADR 0008](adr/0008-permitted-training-image-sources.md)). An unknown licence
or unknown commercial-use rights is a refusal, enforced by the database, not
merely by the command.

| Source | Can it ever carry a grade? |
| --- | --- |
| `first_party` / `photographed_before_submission`: raw cards this project owns, photographed and then submitted | **Yes.** This is the primary source, and the only one that produces training labels for the grade models. |
| `first_party` / `photographed_owned_slab`: slabs already owned | Yes, but the card is photographed through the slab, which is not the domain the product sees. **Train split only** — see below |
| `contributed` / `contributed_under_written_grant` | Only if the contributor supplies the grade and the grant covers it |
| `product_upload` / `uploaded_by_user_with_consent`: trial photographs a user agreed to keep | **No.** It helps the condition analyzers and the gate, never the grade models |

**A slab-sourced copy never enters the test split.** ADR 0008 approves the class
and scores it **0 on domain match**: photographed through the case, it is not
what §11 receives. One in the test split would turn §27's within-±1 Wilson bound
into a measurement of something the product does not do.

**Nothing enforces that.** `acquisition_method` carries no CHECK, it is not one
of §32's grouping keys, and `ml/evaluation` parses it onto `CorpusMember`
without ever reading it back — so the splitter is as likely to put a slab card
in `test` as anywhere else. Keeping them out is a purchasing discipline, and
`tcg-publish-dataset-version` is where it is checked: the publish log prints the
provenance mix **per split**.

Both published versions are clean — every image in `pokemon-condition-v0.1.0`
and `v0.2.0` is `photographed_before_submission`, on all three splits.

One invocation is one physical card:

```bash
uv run tcg-ingest-training-images --front front.jpg --back back.jpg --source first_party --acquisition-method photographed_before_submission --license "owned outright" --commercial-use-allowed --derivative-use-allowed --acquired-at 2026-08-01T10:00:00+08:00
```

**A submission batch is a manifest, not a hundred invocations.** #311's first
batch is about 112 cards per company, so one command reads a CSV that pairs
whatever your photographs happen to be called:

```csv
front,back,label
IMG_4471.HEIC,IMG_4472.HEIC,charizard-base-4
DSC_0099.jpg,DSC_0101.jpg,pikachu-jungle-60
```

```bash
uv run tcg-ingest-training-batch --manifest cards.csv --timezone +08:00 --source first_party --acquisition-method photographed_before_submission --license "owned outright" --commercial-use-allowed --derivative-use-allowed
```

- **It loops one card's transaction; it never widens it.** A row that fails is
  logged by number and skipped, and the rows before it stand.
- **Re-running is safe.** A photograph already in the corpus is a refusal
  (`uq_training_images_sha256`), so a resumed run skips what landed.
- **Whatever Pillow can decode becomes a JPEG or a PNG.** A HEIC straight off a
  phone converts to lossless PNG; a file that is already JPEG or PNG is stored
  byte for byte, never re-encoded.
- **`acquired_at` comes from EXIF `DateTimeOriginal` plus `--timezone`**, since
  EXIF records no offset. A row may state its own instead. A photograph with
  neither is refused rather than stamped with the time it was ingested.
- **It writes `cards.ingested.csv` beside the manifest**, one line per row with
  the `physical_copy_id` the grade will be recorded against weeks later. That
  file is the mapping step 4 needs — keep it with the photographs, outside the
  repository.
- **HEIC needs the `worker` extra**: `uv sync --all-packages --extra worker`, or
  the worker image. The API image has no caller for a HEIF decoder and does not
  carry one.

**Keep the physical-copy identifier it prints** (the batch writes them all to its output CSV). The grade that comes back
weeks later is recorded against it. Keep the mapping from card to identifier
outside the repository, beside the photographs.
[Training images](database.md#training-images) has the rest: adding a later
session's photographs, and cards already slabbed.

### 2. Normalize, then find near duplicates

```bash
uv run tcg-normalize-training-images
uv run tcg-detect-duplicate-training-images
```

**Normalization** produces the straightened card that annotations are measured
on. It never replaces an artifact that already exists
([Normalizing a training image](database.md#normalizing-a-training-image)).

**The duplicate pass** links photographs that are near copies of each other and
deletes nothing. The split then keeps them on one side of the train/test
boundary
([Detecting near-duplicate training images](database.md#detecting-near-duplicate-training-images)).

### 3. Annotate the condition

The annotation tool records centering, corner, edge and surface marks, each
with the annotator's confidence; `unknown` is always a valid answer. It never
records a grade. Run the API against the corpus and the tool beside it:

```bash
docker compose -f infrastructure/local/docker-compose.yml stop api annotation
uv run uvicorn tcg_api.main:app --port 8000
pnpm --filter @tcg/annotation dev
```

The tool is on <http://localhost:3001>. Annotations are append-only: a
correction is a new row, so a published version keeps meaning what it meant
([Annotating a training image](database.md#annotating-a-training-image)).

### 4. Submit the card, and record what comes back

Send the physical card to PSA, TAG or BGS. When the slab returns, record the
grade against the copy identifier from step 1:

```bash
uv run tcg-record-grading-outcome --physical-copy <copy id> --company psa --certification-number 12345678 --grade 9 --returned-at 2026-09-30
```

**This is the only ground truth the grade models accept.** A grade is checked
against the issuing company's own scale: PSA and TAG issue no 9.5, and BGS
does. See
[Recording a grading submission's outcome](database.md#recording-a-grading-submissions-outcome).

### 5. Publish a dataset version

```bash
uv run tcg-publish-dataset-version --version pokemon-condition-v0.3.0 --seed 1
```

This freezes the corpus as it stands, in one transaction:
- **It splits by physical card before anything else.** A card's front, back and
  near duplicates never land on opposite sides of a train/test boundary.
- **A published version can never be edited.** Retraining on more data means a
  new version.
- **The log names each split's provenance mix.** A slab-sourced image in `test`
  is visible there and nowhere else.
- **The manifest in `datasets/manifests/`** holds identifiers and hashes, never
  images. It is what a training run reads, instead of the database
  ([ADR 0009](adr/0009-the-dataset-store-as-a-database-domain.md)).

`--regenerate` reproduces the manifest byte for byte
([Publishing a dataset version](database.md#publishing-a-dataset-version)).

### 6. Train (not built)

No training code exists yet. When it does, a model must:
- **be one model per company**, taking the neutral condition reading and
  returning a full distribution over that company's own grade scale;
- **read a published manifest,** never the database or `/latest/`;
- **fit calibration on train and validation only.** The test split exists to be
  scored, never tuned against;
- **keep its weights in object storage.** Weights never enter git; the
  repository refuses `*.pt`, `*.onnx` and `*.safetensors`;
- **record, for every change,** the dataset version, the metrics, the model
  version, the inference schema, a reproducible artifact and the git commit.

It plugs in behind the same interface the baseline uses: a predictor injected
into the company's adapter, versioned `grading-psa-v0.2.0` rather than
`grading-psa-heuristic-v0.1.0`.

### 7. Test

```bash
uv run tcg-evaluate-condition --version pokemon-condition-v0.3.0
uv run tcg-evaluate-grading --version pokemon-condition-v0.3.0
```

**What each one measures:**
- **The condition evaluation** scores the four analyzers against the
  annotations, split by split, with Brier score, log loss, expected calibration
  error and reliability bins. See
  [`ml/evaluation/README.md`](../ml/evaluation/README.md).
- **The grade evaluation** scores each company's predictions against the
  recorded grades: exact accuracy, accuracy within one step of the company's
  own scale, the confusion matrix, the same calibration figures, and the 95%
  Wilson lower bound on the within-one accuracy.

**How the results are recorded:**
- Each run writes one experiment record to `ml/evaluation/experiments/`.
- **A record is never overwritten.** A rerun at the same versions is refused,
  and that refusal is the signal that nothing changed.
- A figure with nothing to measure refuses with a reason instead of printing a
  number. That is why today's grade record reads `insufficient_information`
  throughout: the test split holds no issued grade.

**A trained model enters only by beating the baseline on this benchmark,
behind the same interface.**

### 8. Register, then serve

```bash
uv run tcg-register-model-bundle --model-name grading-psa --model-version grading-psa-v0.2.0 --training-dataset-version pokemon-condition-v0.3.0 --training-config config.json --metrics metrics.json --artifact-location model-bundles/grading-psa-v0.2.0/weights.pt
```

**Registration.** Every bundle is born `experimental` and only moves forward:
`candidate`, then `production`, then `retired`. There is one `production`
bundle per model name, and a row is never deleted
([Registering a model bundle](database.md#registering-a-model-bundle)).

**Serving the bundle to users** is the part not built: there is no promotion
command, and analyses are not yet linked to registry rows.

## What "good enough" means

[ADR 0011](adr/0011-the-v1-grade-predictor-basis.md) holds a grade model to
**80% of predictions within one grade of the issued grade, judged on the 95%
Wilson lower bound** (spec §27), not on the raw rate.

The bound is what sets the size of the job. Even a perfect record needs **at
least 16 graded cards in the test split per company** before it clears 0.80;
four out of four gives a bound of 0.51. Here is the arithmetic:

1. The splitter aims at 70 / 15 / 15 **by physical card** and does not force
   it, so 16 test cards means about 107 graded cards per company. **Plan on
   about 112**, which leaves room for a split that lands short.
2. A card sits in one slab at a time (crack-and-resubmit is outside V1), so the
   three companies need about **330 cards between them**.
3. That assumes no mistakes. A model that makes some needs more:

   | Misses on the test split | Test-split grades needed | Graded cards at a 15% test share |
   | --- | --- | --- |
   | 0 | 16 (bound 0.806) | about 107 |
   | 1 | 25 (bound 0.805) | about 167 |
   | 2 | 33 (bound 0.804) | about 220 |

   Computed with `tcg_ml_evaluation.metrics.wilson_lower_bound` at z = 1.96.
4. Each card costs a grading fee and weeks to months of turnaround.

The binding constraint is graded cards, not code.

A trained model re-enters the product the moment `grading_outcomes` holds
test-split rows. The 0.50 confidence gate and the 80% target do not move to
meet the data; changing either is a new ADR.

## The first submission batch

Decided on 2026-09-14 (#311).

**TAG goes first.** BGS gets a two-card tracer; PSA is deferred.
- **TAG** issues whole grades, so "within one step" is judged on the same
  scale as PSA. It is also the cheapest per card through Singapore's only
  authorised TAG dealer (Newtro).
- **BGS** issues half grades, so one step is half a grade and the ±1 bar is
  the hardest of the three. Two cards prove the round trip and the half-grade
  path through `tcg-record-grading-outcome` before any volume goes that way.
- **PSA** takes no direct submissions from Singapore; they go through a
  middleman (Oxley, SGCardBros). Its cheapest tiers are paused until about
  October 2026.

**The first round is 12 cards, and every one is already in
`pokemon-condition-v0.2.0`.** They are photographed, ingested and annotated
under `photographed_before_submission`, so no new photography is needed.

| Company | Card | Set · number | Split in v0.2.0 |
| --- | --- | --- | --- |
| TAG | Bulbasaur AR (JP) | M1L 064/063 | validation |
| TAG | Clobbopus AR (JP) | sv7a 072/064 | train |
| TAG | Ivysaur AR (JP) | M1L 065/063 | train |
| TAG | Misty's Lapras AR (JP) | sv9a 072/063 | train |
| TAG | Meowth AR (Simplified Chinese) | CBB3C 0207/07 | train |
| TAG | Mew ex SAR (Simplified Chinese) | 151C 191/151 | validation |
| TAG | Team Rocket's Mimikyu AR (JP) | M2a 205/193 | train |
| TAG | Misty's Psyduck AR (JP) | sv9a 071/063 | train |
| TAG | Mega Venusaur ex SAR (JP) | M1L 087/063 | **test** |
| TAG | Wally's Compassion SAR (JP) | M1S 091/063 | train |
| BGS | Pikachu ex SAR (JP) | M2a 234/193 | train |
| BGS | Milotic, Trainer Gallery (EN) | Silver Tempest TG02/TG30 | train |

What the round buys, stated plainly:
- **It yields one TAG test-split grade**, not sixteen, so §27 stays
  unclaimable after it. It is the first tranche toward about 112 TAG grades,
  not the plan that reaches them.
- **A later version can move cards between splits.** The column above is
  v0.2.0's assignment; a version published with more cards re-splits the whole
  corpus.
- **The two Chinese cards are outside V1**, which covers English and Japanese.
  Their grades are recorded, but they are not in-scope evidence for §27.
- **Two cards stay raw:** Ditto (MEW 132/165) and Feraligatr (Expedition
  12/165). Not every photographed card is submitted. A card that is never
  shipped is condition data only and never carries a grade.

### Cost and turnaround

A snapshot taken 2026-09-14 at US$1 = S$1.27. **Every cheap tier at all three
companies is paused, so reconfirm each figure with the dealer before shipping.**

| Company | Tier | Grading fee | All-in per card, Singapore | Turnaround (business days, excluding transit) | State on 2026-09-14 |
| --- | --- | --- | --- | --- | --- |
| TAG | Basic (10-card minimum) | US$22 | about S$33 (Newtro) | 45+ | paused |
| TAG | Standard | US$39 | not quoted | 30 | paused |
| BGS | Base (no subgrades / all four) | US$14.95 / US$17.95 | S$60–100 (Newtro) | 75+ | reopens 2026-09-15 |
| BGS | Standard | US$34.95 | S$60–100 (Newtro) | 45 | reopens 2026-09-15 |
| PSA | Value Bulk (Collectors Club US$149 a year, 20-card minimum) | US$24.99 | S$51–70 (Oxley) | 95 | paused until about October |

- **The first round is 10 × TAG Basic plus 2 × BGS Base.** That comes to about
  **S$450–650** with the dealer's fee and return shipping.
- **At 112 cards, the TAG plan is about S$3,700 at Basic.**
- **Cadence.** The round ships on the first Newtro cutoff (the last Sunday of
  each month) after TAG Basic and BGS Base reopen. Later TAG tranches follow
  monthly as cards are bought and photographed.
- **Turnaround, door to door:**
  - TAG Basic: about 3–4 months.
  - BGS Base: about 4–5 months.
  - That includes the wait for the cutoff and shipping both ways.
  - **#314 should plan against the first TAG grades no earlier than about four
    months after the round ships.**

Sources: [TAG pricing](https://taggrading.com/pages/pricing) and
[a tier summary](https://cardgrade.io/tag-card-grading-cost);
[BGS tiers and pauses](https://cardgrade.io/blog/bgs-grading-cost-breakdown);
[PSA tiers and pauses](https://allvintagecards.com/psa-grading-costs/);
[Singapore all-in costs](https://tcgtalk.com/guides/pokemon-card-grading-costs-singapore)
and [dealers](https://tcgtalk.com/guides/psa-grading-services-singapore);
[Newtro's grading service](https://newtroshop.com/grading-made-simple).
The Singapore figures are community-reported.

### Before a card leaves the house

The mapping from card to physical-copy id stays **outside the repository,
beside the photographs**:
- `copies.csv` for the v0.2.0 cards;
- `cards.ingested.csv` for any batch that `tcg-ingest-training-batch` ingests.

For every card:
1. **Photograph** the front and back.
2. **Ingest** both photographs.
3. **Check that the card's `physical_copy_id` is in the mapping file.**
4. Only then put the card in the submission.

No card ships unphotographed or without its copy id. When the slab comes back,
record the grade against that id (step 4 of the loop).

## Where trial data fits

- **Consented photographs** from a trial are
  [ADR 0008](adr/0008-permitted-training-image-sources.md)'s fourth class.
  - They land in the host's own database, not in `tcg_corpus`, and nothing
    moves them between the two yet.
  - They never carry a grade, so they can improve the condition analyzers and
    the gate once annotated, never the grade models.
- **Reported grades** are a label with no features: no photograph, no physical
  card.
  - [Reviewing](database.md#reviewing-a-grade-a-user-reported) one marks it
    validated or rejected, and that is all.
  - Two tests keep the feedback domain out of `tcg_api/datasets/` and `ml/*`,
    so nothing retrains on a report automatically.
  - A validated report becomes evidence only through
    `tcg-record-grading-outcome`, and only for a card whose photographs the
    corpus already holds under an approved source.
  - Their natural use is scoring the product's predictions against what users
    received (spec §26), once there are enough of them.

## Not built yet

- A training script per company.
- A promotion and retirement path for registered bundles, and a link from an
  analysis to the registry row it used.
- A way to move consented photographs from the host into the corpus.
- A learned condition analyzer. It would enter through the same benchmark or
  not at all.

## Open risks

- **Rights in the card artwork are unresolved** (ADR 0008, risk R1). No dataset
  is ever published, and a manifest carries no image.
- **Licensing training images from the grading companies is not approved,**
  because nobody has been asked yet. #151 is the outbound request.
- **Grading turnaround sets the pace.** No label arrives faster than a slab
  comes back.
- **Paused tiers set the start date.** On 2026-09-14 the cheap tiers at all
  three companies were closed to new submissions, so the first round cannot ship
  until they reopen (see [The first submission batch](#the-first-submission-batch)).
- **ADR 0011 is due for review on 2026-11-22,** or sooner at the first
  test-split grade or the first registered trained bundle.
