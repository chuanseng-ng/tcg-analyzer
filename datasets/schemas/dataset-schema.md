# The dataset, provenance and membership schema

Four tables, described here for a human. **The DDL is
[the migration](../../database/migrations/versions/20260828_add_the_dataset_and_provenance_schema.py)**
and the declaration is
[`services/api/src/tcg_api/datasets/tables.py`](../../services/api/src/tcg_api/datasets/tables.py);
this file is documentation, and a line here that disagrees with either of them
is stale — see [ADR 0009](../../docs/adr/0009-the-dataset-store-as-a-database-domain.md).

Image bytes live in object storage. A row carries the storage key and the
`sha256`; the bytes are never a column and never in git.

## `physical_copies` — one physical card

Spec §32 requires a train/validation/test split that does not leak, by grouping
on the physical card, the source, the card instance or the slab. **None of spec
§29's nine provenance fields identifies a physical copy**, which is the gap
[ADR 0008](../../docs/adr/0008-permitted-training-image-sources.md) found while
filling those fields in. This table closes it.

| Column | Meaning |
| --- | --- |
| `id` | The per-copy identifier, assigned when the card is acquired. |
| `certification_company` | `psa`, `tag` or `bgs`, where the copy has been slabbed. |
| `certification_number` | The number printed on the slab. |
| `created_at` | |

The pair `(certification_company, certification_number)` is unique, and either
both are present or neither is. Two copies with no certification are two rows:
`NULLS NOT DISTINCT` is deliberately not set, so nothing collapses every
unidentified copy into one.

**The catalog card is not on this table.** Two copies of one Base Set Charizard
share a `card_id` and must be splittable apart; one copy photographed twice
shares no `sha256` and must not be. Which card a copy depicts is recorded on the
image, which is the row that can exist before anybody has identified it.

**This table is not write-once**, unlike a dataset version. A certification
number arrives weeks after the photographs do.

### Deriving the identifier, per approved source

ADR 0008 approves four sources and no others. Each one answers "which physical
object is this?" differently, and one of them cannot answer it at all.

| Approved source | Identifier | How it is derived |
| --- | --- | --- |
| 1 — photographs we take of raw cards we own, then submit for grading | `physical_copies.id`, then the certification number | A copy row is created when the card is photographed. The submission's certification number is written back onto the same row when it returns, so the pre- and post-grading photographs group together. |
| 2 — photographs we take of graded slabs we own | The certification number | Printed on the slab and visible in the photograph itself. The copy row carries it from the start. |
| 3 — photographs contributed under a written grant | `physical_copies.id`, from the contributor's own copy reference | The [grant template](../documentation/) asks the contributor which photographs are of the same card; one copy row per distinct card they name. Where a contributed photograph is of a slab, its certification number goes on the row and takes over. |
| 4 — this product's own user uploads, where the user consented | **None — the grouping falls back to `source`** | See below. |

**Class 4 has no per-copy identifier, and says so.** §29's record for a consented
upload references the `analysis_id`, which groups the front and back of one card
but not two analyses of the same card by the same person — and nothing in an
anonymous session (spec §53) can tell those apart. `training_images.physical_copy_id`
is therefore NULL for this class and the splitter groups it by `source`, which
§32 lists among its acceptable keys precisely for this case. A grouping key that
is honestly coarse beats one that is confidently wrong. If the consent mechanism
turns out to carry something that identifies a copy, that is a refinement of this
row rather than a correction of it.

## `training_images` — one image and the rights that came with it

Spec §29's nine fields are columns on this row, beside the digest, which is what
lets the ingestion gate be a constraint rather than a function a loader must
remember to call.

| §29 field | Column | Notes |
| --- | --- | --- |
| `source` | `source` | `first_party`, `contributed` or `product_upload`. Also §32's fallback grouping key. |
| `source_url/reference` | `source_reference` | The specification spells this one field two ways and neither is a legal column name. |
| `acquisition_method` | `acquisition_method` | How, where `source` is who. |
| `license` | `license` | Ownership, the grant by identifier and date, or the consent text by version. |
| `commercial_use_allowed` | `commercial_use_allowed` | Read by the gate. |
| `derivative_use_allowed` | `derivative_use_allowed` | Read by the gate. |
| `redistribution_allowed` | `redistribution_allowed` | Recorded, never gated. |
| `permission_notes` | `permission_notes` | The grant's own limits, the consent version, ADR 0008's standing risk R1. |
| `acquired_at` | `acquired_at` | When the photograph was taken, not when the row was written. |

Beside them: `physical_copy_id`, `card_id`, `side`, `original_uri`, `sha256`,
`mime_type`, `width`, `height`, `normalized_uri`, `normalization_details` and
`created_at`.

### The artifact, and why it is a column

`original_uri` names the photograph. `normalized_uri` names the standardized
standardized artifact spec §30's annotation tool shows and §21's coordinates are
fractions of, and `normalization_details` records how it was made — the
projective transform, the quarter-turn, the artifact's size, the stage version
and its thresholds, which is `images.normalization_details`' shape exactly.

Both are nullable and both stay NULL until `tcg-normalize-training-images` has
run, and afterwards too where no card was located: there was nothing to
straighten, and the tool renders the photograph while saying so.

**`width` and `height` on this table are the photograph's**, and are NOT NULL —
unlike `images.width`, which holds the artifact's. The artifact's size therefore
lives in the details rather than overloading a pair that already means something
else here. And **a stored artifact is never replaced**: an annotation is a
fraction of the artifact its annotator saw, so re-warping one under a bumped
normalizer would move every stored coordinate without touching a row in
`image_annotations`.

### The gate

```sql
CONSTRAINT ck_training_images_provenance_permits_training CHECK (
    commercial_use_allowed IS TRUE
    AND derivative_use_allowed IS TRUE
    AND license IS NOT NULL
    AND btrim(license) <> ''
)
```

ADR 0008's rule is that **a null, an empty string and an absent field are one
answer, and it is refusal**. Three things about this constraint follow from that
and are not free to change:

- **`IS TRUE`, never a bare column reference.** `NULL AND true` is `NULL` in SQL,
  and a `CHECK` **passes** on `NULL` — so the obvious spelling would admit
  exactly the unknown-provenance image the gate exists to refuse.
- **The three columns it reads are nullable.** `NOT NULL` would refuse a null
  under a different constraint with a different message; one refusal with one
  name is what makes the rule reviewable.
- **`redistribution_allowed` is not in it.** ADR 0008 makes it `false` on all
  four approved sources, including the photographs this project took itself,
  because the artwork is not ours. The column records that answer and is not a
  switch to be waived — which is why **no dataset is ever published** and why a
  manifest of identifiers and hashes is all a version leaves behind.

`source` carries no membership `CHECK`, following `grading_rules.company`: the
allow-list is enforced in the ingestion path, where it changes, and the rights
are enforced here, where they do not. A fifth approved source costs an ADR and
no migration. That allow-list is `APPROVED_SOURCES` in
[`tcg_api/datasets/ingestion.py`](../../services/api/src/tcg_api/datasets/ingestion.py),
and it is keyed on the **pair** `(source, acquisition_method)` — classes 1 and 2
are both `first_party`, and what separates them is whether the card was raw or
already slabbed.

`sha256` is unique here, where `images.sha256` in the analysis domain
deliberately is not — the same photograph uploaded to two analyses is two
images, and the same photograph ingested twice is one training image. That is
the exact-duplicate half of deduplication; the near half is
`training_image_fingerprints` below, which is its own table because a hash is
derived data rather than a fact about provenance.

`source_reference` holds a consented upload's analysis identifier as **text and
not a foreign key**: spec §54 deletes that analysis on schedule and the training
image outlives it.

## `training_image_fingerprints` — the near-duplicate half

Spec §32 forbids splitting near-identical photographs of one card across train
and test. `uq_training_images_sha256` catches the case where the bytes are
identical; a retake under different light is a different digest and the same
card, which is exactly the leakage §32 names. This table is that half.

| Column | Meaning |
| --- | --- |
| `training_image_id` | Primary key and foreign key both — a derived row has no identity of its own. `ON DELETE CASCADE`. |
| `perceptual_hash` | A 64-bit difference hash over the **normalized artifact**, as 16 lowercase hex characters. `NULL` when no card could be located. |
| `perceptual_hash_rotated` | The same hash of the artifact turned 180 degrees. |
| `hash_version` | The hash, the detector and the normalizer, composed. A row whose version is stale is recomputed. |
| `computed_at` | |

**The hash is taken over the artifact, not the photograph.** `ml/normalization`
has already removed framing and perspective, so two shots of one card from
different angles compare as the same card without this table knowing anything
about geometry.

**Two hashes, because orientation is genuinely unknown.**
`Normalized.quarter_turns` puts the card's short edge first and makes no claim
about which way up the card is *printed* — only reading the artwork could say —
so an artifact is in exactly one of two orientations. The second hash is
computed rather than derived: a 180-degree turn reverses the direction of the
left-to-right comparison the hash is built from, so it is not a bit reversal of
the first. Canonicalising the two into one is the obvious wrong simplification,
because two near-identical artifacts can canonicalise to opposite orientations.

**Both hash columns are nullable, together.** An image the detector finds no card
in yields no artifact and therefore no hash, and the row is written anyway under
the version that examined it — that is an answer rather than a gap, and it is
what stops the next pass decoding those bytes again. `both_hashes_or_neither`
keeps it honest.

**`ON DELETE CASCADE`, where every other key into `training_images` restricts.**
`dataset_members` restricts because §31 means a version cannot un-include an
image. A hash means nothing without the bytes it describes, so it must never be
the reason a row cannot be removed.

**Pairs and groups are deliberately not stored.** A duplicate relationship is a
pure function of two hashes and a threshold, the threshold is not persisted, and
a stored pair is a second answer that drifts from the first the moment the number
moves — the same argument `market_snapshots` makes for deriving its membership
from a cut-line. The relationship is computed by
[`tcg_api/datasets/fingerprints.py`](../../services/api/src/tcg_api/datasets/fingerprints.py),
which imports no CV stack precisely so §32's splitter can consume it.

**What the hash cannot do, stated rather than hidden.** It is designed to
collapse what two copies of one printing share — the artwork — and condition
differences are sub-pixel at this scale, so two different copies of one common
card are reported as near duplicates. That is the safe direction: over-grouping
costs a little balance in the split, under-grouping leaks. Tightening the
threshold until such a pair separated would make it tight enough to miss a real
retake.

Not write-once: a detector or normalizer version bump rewrites every row, so this
table carries no immutability trigger.

## `dataset_versions` — one frozen corpus

Spec §31 requires every training run to reference a `dataset_version` such as
`pokemon-condition-v0.3.0`, and forbids a model referencing `/latest/`. The
grammar is a `CHECK`, so `/latest/` is not storable.

| Column | Meaning |
| --- | --- |
| `id` | A surrogate key; the identity is `version`. |
| `ordinal` | Publication order, `GENERATED ALWAYS`. The current version is the highest ordinal — a query, never a mutable pointer. |
| `version` | `pokemon-condition-v0.3.0`. |
| `split_seed` | The seed the splitter ran with. |
| `created_at` | |

**The achieved split proportions are not stored.** They are a count over
`dataset_members`, and a stored copy is a second answer that can drift from the
first. The seed is stored because it is derivable from nothing, and a split that
cannot be reproduced makes a version reproducible in name only.

A version is write-once: `trg_dataset_versions_immutable` refuses an `UPDATE`.

## `dataset_members` — one image's place in one version

| Column | Meaning |
| --- | --- |
| `dataset_version_id` | `ON DELETE CASCADE`. Part of the primary key. |
| `training_image_id` | `ON DELETE RESTRICT`. Part of the primary key. |
| `split` | `train`, `validation` or `test`. |
| `created_at` | |

**This is a real membership list, and that differs from `market_snapshots` on
purpose.** A market snapshot stores no members because its membership is
derivable from a cut-line on `created_at`; a train/validation/test assignment is
a *decision* and is derivable from nothing.

`training_image_id` is `RESTRICT` because ADR 0008 grants retention after a
contributor withdraws, precisely because §31 means a version cannot un-include
an image. Deleting one would leave a manifest naming bytes nobody can produce.

The primary key is the pair, so an image appears in a version once, in one
split — an image in two splits of one version is the leakage §32 is about.

## Splitting a corpus — spec §32

The assignment `dataset_members.split` records is decided by
[`services/api/src/tcg_api/datasets/splitting.py`](../../services/api/src/tcg_api/datasets/splitting.py),
which stores nothing: it reads the grouping keys and the fingerprints and returns
an assignment, and the versioning issue writes that assignment and its version in
one transaction.

**It groups first and splits the groups.** Assigning images and repairing
collisions afterwards is the same bug with more steps. Three relations put two
images in one group, and they compose:

| Relation | Where it comes from |
| --- | --- |
| A shared `physical_copy_id` | §32's primary key, and the reason `physical_copies` exists. |
| A shared `source`, where `physical_copy_id` is NULL | §32's documented fallback — see the per-source table above; it is approved class 4 and nothing else. |
| A near-duplicate group | `training_image_fingerprints`, so two images the provenance did not link but the hash did stay together. |

Because they compose, one hash linking a consented upload to a first-party copy
merges that whole source group with the copy. The merge is unbounded and it is
the safe direction — over-grouping costs a little balance, under-grouping leaks —
and the result reports its group sizes largest-first so a runaway group is
visible rather than silent.

**The order is seeded with a hash, not with a PRNG.** Groups are considered in
order of `sha256(seed:smallest member id)`, which is stable across Python
versions, platforms and languages; `random.shuffle`'s sequence is an
implementation detail nobody promised, and `split_seed` exists so a split can be
re-derived years later. Each whole group then goes to whichever split is furthest
below its share, measured in exact fractions.

**The proportions are targeted, not forced.** Groups have different sizes, and a
split that hits 70/15/15 exactly has almost certainly broken one apart. What was
achieved comes back on the result and is countable from `dataset_members`; a
corpus small enough that a split comes back empty is logged as a warning rather
than fixed by splitting a group.

Grades are not stratified over. That needs grades to exist, which is M8's, and
only if the corpus is large enough for a stratum to mean anything.

## Versioning a corpus

```bash
uv run tcg-publish-dataset-version --version pokemon-condition-v0.1.0 --seed 20260828
uv run tcg-publish-dataset-version --version pokemon-condition-v0.1.0 --regenerate
```

Spec §31 requires every training run to reference a `dataset_version` and forbids
a model referencing `/latest/`, which is why the identifier carries a CHECK on its
grammar rather than a convention. Publishing writes the version row, every
member's split and the seed **in one transaction**. Both tables refuse an `UPDATE`
in a trigger, so a frozen version cannot be edited; that a member cannot be
*added* afterwards is held by there being no code path which does it — the members
are written by the transaction that created the version and by nothing else. A
re-split is a new version.

**The manifest is a render of the rows, never a record.** The counts, the achieved
proportions and the per-source provenance mix are recomputed from
`dataset_members` on every render, so `--regenerate` reproduces the file byte for
byte and none of the three is a column — the same relationship `market_snapshots`
has with its derived `data_version`. `split_seed` is stored because it is the only
one derivable from nothing.

A version over an empty corpus is refused: §31's point is that the reference means
something, and a version with no members resolves to nothing.

## What is not here

Annotations are their own tables and their own migration — see
[the annotation schema](annotation-schema.md).
`datasets/manifests/` holds what a version leaves behind, generated from these
rows by the command above.

The ingestion path is
[`services/api/src/tcg_api/datasets/ingestion.py`](../../services/api/src/tcg_api/datasets/ingestion.py),
reached through `uv run tcg-ingest-training-images`. It reuses the upload
validation the analysis domain already owns rather than carrying a second copy,
and it verifies provenance *before* storing anything, per spec §28's ordering.
