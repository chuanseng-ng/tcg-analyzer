# `datasets/manifests`

Immutable, versioned manifests listing the images in each dataset version by
identifier and content hash, together with their train/validation/test split.
**Generated** from the dataset domain in the database, never hand-written — see
[ADR 0009](../../docs/adr/0009-the-dataset-store-as-a-database-domain.md).

**Manifests only — never images.**

```bash
uv run tcg-publish-dataset-version --version pokemon-condition-v0.1.0 --seed 20260828
uv run tcg-publish-dataset-version --version pokemon-condition-v0.1.0 --regenerate
```

The first freezes the corpus as it stands: it creates the `dataset_versions`
row, writes every member's split in the same transaction, and renders
`pokemon-condition-v0.1.0.json` here. The second re-renders an existing version
and writes nothing to the database.

**A manifest is a render of the rows, not a record.** The counts, the achieved
proportions and the per-source provenance mix are recomputed from
`dataset_members` on every render, which is why `--regenerate` reproduces the
file byte for byte and why none of the three is a column. Only `split_seed` is
stored, because it is the only one derivable from nothing. Nothing is stamped at
render time either — no generated-at, no application version — since either would
make the first regeneration differ from the file it replaced.

**Each member also carries its annotation rows** (#188): every
`image_annotations` and `centering_measurements` row for the image, ordered by
`(created_at, id)` and never collapsed — the newest row per `(kind, region)`
being the current view is the *reader's* rule, applied by `ml/evaluation`.
`annotator_id` and `notes` stay out of the committed file. Because those tables
are append-only rather than frozen, the byte-identity invariant is
same-database-state → same-bytes: annotating an image after its version was
published changes the next render, which is a regeneration to re-commit, not
drift.

**Each member also carries its physical copy's grading outcomes** (#220): every
`grading_outcomes` row for the copy the image is of — `id`, `company`,
`certification_number`, `grade` *or* `designation` (an absent key where the
company issued the other), `created_at` — ordered by `(company,
certification_number)` and repeated on the front and the back, because the
label belongs to the card and the manifest is keyed by image. That repetition
is the price of a member that describes itself: the file still names no
`physical_copy_id`, and a top-level map keyed by copy would have needed one.
The newest outcome per company being the issued grade is again the reader's
rule (`tcg_ml_evaluation.truth.issued_grades`). The four BGS subgrades and
`returned_at` are deliberately not rendered — nothing reads them, and a field
is a regeneration away when something does. An old file is refused on key
presence rather than read as an unlabelled corpus.

**A grade is publishable under ADR 0008, and this is the determination #165
deferred.** `redistribution_allowed` is `false` on every approved source because
the *artwork* on the photograph is not ours; a grade reproduces no artwork and
no text. It is a fact about property this project owns — what a company issued,
to us, for our card — for approved classes 1 and 2, and for class 3 it is a fact
the contributor supplied under the grant, whose template already asks for the
certification company and number so the grade can be verified on the issuing
company's own public lookup. Class 4 has no physical copy and therefore no
outcome. The certification number carries no more than the slab prints on its
face and the company publishes in its lookup. Nothing here changes the answer
for the image bytes: those stay in object storage and out of git.

**This is what a dataset version may leave behind, and all of it.** ADR 0008
makes `redistribution_allowed` false on every approved source, including the
photographs this project took itself, so no dataset produced under it is ever
published. Identifiers and content hashes carry no artwork, so a manifest may be
committed; the bytes live in object storage and are never a column and never in
git. Each member carries its storage key and its `source`/`acquisition_method`
pair as well, so a training run can resolve the file without reading the database
— ADR 0009 requires `ml/*` to read the manifest rather than PostgreSQL.

**A version number is its own commit**, per the repository's convention on
immutable artifacts: `chore(datasets): pin dataset version pokemon-condition-v0.1.0`.
