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
