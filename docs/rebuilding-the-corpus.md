# Rebuilding the training corpus

The runbook for a lost `tcg_corpus`. It happened on 2026-09-07, when the local
Compose volumes were recreated (the same outcome as `down -v`). Nothing in
Compose provisions or backs up the corpus (#196), so this is the recovery.

**What comes back** is everything a committed manifest in
[`datasets/manifests/`](../datasets/manifests) records: image ids, content
hashes, storage keys, splits, every annotation and centering row (with their
original ids and `created_at`), grading outcomes, and each version's id,
ordinal, seed and `created_at`.

**What does not come back:**
- anything annotated or recorded after the last published version;
- annotation `notes`, `metadata` and polygons;
- the original `annotator_id` (one is supplied);
- certification columns on `physical_copies`, and BGS subgrades and
  `returned_at` on outcomes (none are rendered);
- `card_id` links;
- near-duplicate fingerprints (recomputed).

## What you need

- **The original photographs, byte for byte.** Each must hash to its member's
  `sha256`: the PNGs that were ingested, not the HEICs they were converted
  from. For v0.2.0 they are in `~/Downloads/tcg-analyzer-test/snapshot/<card>/`.
- **The committed manifests**, one per version to restore.
- **Each photograph's `physical_copy_id` and `acquired_at`**, which no manifest
  carries. For v0.2.0:
  - the copy ids are in `snapshot/copy-id-mapping.md`;
  - the instants are the HEIC originals' EXIF `DateTimeOriginal`, at `+08:00`.
- **The provenance statement** the images were ingested under. For v0.2.0 that
  is `first_party` / `photographed_before_submission`, `--license "owned outright"`,
  with commercial and derivative use allowed.

**Never point any of this at `tcg`, and never run pytest at `tcg_corpus`.**

## 1. An empty corpus database

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
docker compose -f infrastructure/local/docker-compose.yml exec postgres createdb -U tcg tcg_corpus
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg_corpus
export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
# The restore and normalization write to MinIO, so they also need the bucket and credentials.
# These are the local defaults from .env.example; use your own if you changed them.
export TCG_API_STORAGE_BUCKET=tcg-local TCG_API_STORAGE_REGION=us-east-1
export TCG_API_STORAGE_ACCESS_KEY_ID=tcg TCG_API_STORAGE_SECRET_ACCESS_KEY=tcglocaldev
uv run alembic upgrade head
```

Every later step assumes these variables are still set in the same shell.

If `createdb` says the database exists, check that it is really empty before
going on. The restore refuses a database holding any corpus row, and that
refusal is the thing to believe.

## 2. The copies CSV

One row per original: `file,physical_copy_id,acquired_at`. `file` is relative
to the CSV's own directory, and `acquired_at` must carry an offset. For
v0.2.0's layout, this writes it beside the photographs. It reads the HEIC EXIF,
so it needs the `worker` extra:

```bash
uv sync --all-packages --extra worker
uv run python - <<'EOF'
import csv, re
from datetime import datetime
from pathlib import Path
from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener()
root = Path.home() / "Downloads/tcg-analyzer-test/snapshot"
copies = dict(re.findall(r"^\| (\w+) \| ([0-9a-f-]{36}) \|", (root / "copy-id-mapping.md").read_text(), re.M))
with open(root / "copies.csv", "w", newline="") as out:
    writer = csv.writer(out)
    writer.writerow(["file", "physical_copy_id", "acquired_at"])
    for card, copy in sorted(copies.items()):
        for png in sorted((root / card).glob("*.png")):
            exif = Image.open(png.with_suffix(".HEIC")).getexif().get_ifd(0x8769)
            taken = datetime.strptime(exif[36867], "%Y:%m:%d %H:%M:%S")
            writer.writerow([png.relative_to(root).as_posix(), copy, f"{taken.isoformat()}+08:00"])
EOF
```

A photograph that matches no manifest member is refused, so list only the
ingested PNGs. `ditto_jp_front_2.HEIC` has no PNG and is correctly absent.

## 3. Restore the rows and the originals

```bash
uv run tcg-restore-dataset-version \
  --manifest datasets/manifests/pokemon-condition-v0.1.0.json \
  --manifest datasets/manifests/pokemon-condition-v0.2.0.json \
  --copies ~/Downloads/tcg-analyzer-test/snapshot/copies.csv \
  --license "owned outright" --commercial-use-allowed --derivative-use-allowed
```

Pass **every** published manifest, so that the ordinals stay true (v0.1.0 is
1, v0.2.0 is 2) and the next publish gets 3. Order doesn't matter.

Before it writes anything, the command refuses:
- a member with no matching photograph;
- a photograph that matches no member;
- two manifests that disagree about an image;
- provenance ADR 0008 does not admit;
- a non-empty target.

It then writes everything in one transaction and puts each original back under
the manifest's `original_uri`. A rerun is a refusal, never a duplicate.

## 4. Normalize at the annotation-era detector, never HEAD

Every annotation's box is a fraction of the normalized artifact its annotator
saw. A newer detector crops differently and moves every box. v0.2.0 was
annotated on `card-detection-opencv-v0.3.0` + `normalization-opencv-v0.2.0`
(commit `d087f98`). On 2026-09-14, HEAD's `v0.15.0` produced a different
artifact for 14 of its 28 images.

For a future manifest, find the commit: take the last detector pin before the
manifest's first annotation `created_at`.

```bash
git log -1 --until=<first annotation created_at> --format='%h %s' -- ml/card-detection ml/normalization
```

Run HEAD's command with that commit's ml packages first on the path:

```bash
git worktree add ../tcg-analyzer-d087f98 d087f98
git diff --stat d087f98 HEAD -- ml/normalization      # empty: only card-detection needs overriding
PYTHONPATH="../tcg-analyzer-d087f98/ml/card-detection/src" uv run tcg-normalize-training-images
```

If `ml/normalization` also changed, put its `src` on `PYTHONPATH` too. The
separator is `;` on Windows and `:` elsewhere.

**`normalization_details` records the normalization version, never the
detector's**, so the database cannot tell you which detector ran. Check the
override **before** normalizing: Python must import the detector from the
worktree, and its source must name the pinned version.

```bash
PYTHONPATH="../tcg-analyzer-d087f98/ml/card-detection/src" uv run python -c "import tcg_ml_card_detection as m; print(m.__file__)"
grep -rhoI 'card-detection-opencv-v[0-9.]*' ../tcg-analyzer-d087f98/ml/card-detection/src | sort -u
```

The first line must print a path inside `../tcg-analyzer-d087f98`, and the
second must print only `card-detection-opencv-v0.3.0`.

After normalizing, check that no artifact is missing and that every one records
the pinned normalization version:

```bash
docker compose -f infrastructure/local/docker-compose.yml exec postgres psql -U tcg tcg_corpus -c "SELECT count(*) FILTER (WHERE normalization_details->>'version' = 'normalization-opencv-v0.2.0') AS pinned, count(*) FILTER (WHERE normalized_uri IS NULL) AS missing, count(*) AS total FROM training_images"
```

`pinned` must equal `total`, and `missing` must be 0.

If the override didn't take and HEAD's detector ran, the artifacts are wrong,
not the annotations. Clear `normalized_uri` and `normalization_details` on
every row, then run this step again.

## 5. Fingerprints

```bash
uv run tcg-detect-duplicate-training-images
```

Fingerprints are not in any manifest and nothing published reads them back.
They matter only to the next publish.

## 6. The gate: regenerate byte for byte

```bash
uv run tcg-publish-dataset-version --version pokemon-condition-v0.2.0 --regenerate
git diff --exit-code datasets/manifests/
```

**The latest version is the gate.** An older one may legitimately differ: a
render includes every annotation on its members, including ones added after that
version was published (`read_manifest` does not filter by date). If the diff is
not empty, `git checkout` the file back and find out why before anything reads
the corpus.

Afterwards:
- `uv run tcg-evaluate-condition` reports zero `no_card_frame` exclusions;
- `uv run pytest` pointed at `tcg_corpus` still refuses (#196);
- `git worktree remove ../tcg-analyzer-d087f98`.

## Doing it without the command

If `tcg-restore-dataset-version` is ever broken, the same rebuild is possible by
hand with `psql` and the MinIO console (`:9001`). Do it in one transaction, in
this order.

1. **`physical_copies`**: one row per distinct `physical_copy_id`, with just the
   `id`.
2. **`training_images`**: per member, the manifest's `training_image_id` (as
   `id`), `sha256`, `side`, `source`, `acquisition_method` and `original_uri`.
   The manifest doesn't carry these, so supply them:
   - `physical_copy_id`;
   - `mime_type` (`image/png`) and `width`/`height` (the photograph's pixels,
     not the artifact's);
   - `license`, `commercial_use_allowed = true`, `derivative_use_allowed = true`,
     `redistribution_allowed = false` (the provenance CHECK refuses anything
     less);
   - `acquired_at`, with its offset.
3. **`image_annotations`**: per entry, `id`, `training_image_id`, `kind`,
   `region`, `label`, `severity`, `confidence`, `representation` and
   `created_at` as written. `bbox.{x,y,width,height}` go to
   `bbox_x`/`bbox_y`/`bbox_width`/`bbox_height`, and an absent key is NULL.
   `annotator_id` is an opaque id such as `annotator`.
4. **`centering_measurements`**: `id`, `training_image_id`, `horizontal`,
   `vertical`, `confidence`, `created_at` and `annotator_id`. An absent axis is
   NULL.
5. **`grading_outcomes`**: per distinct outcome `id`, with `company` →
   `grading_company`, `certification_number`, `grade` or `designation`, and
   `created_at`, on the image's `physical_copy_id`.
6. **`dataset_versions`**: `ordinal` is `GENERATED ALWAYS`, so:

   ```sql
   INSERT INTO dataset_versions (id, ordinal, version, split_seed, created_at)
   OVERRIDING SYSTEM VALUE VALUES ('<id>', <ordinal>, '<dataset_version>', <split_seed>, '<created_at>');
   ```

   Then, after the last version:

   ```sql
   SELECT setval(pg_get_serial_sequence('dataset_versions', 'ordinal'),
                 (SELECT max(ordinal) FROM dataset_versions));
   ```
7. **`dataset_members`**: per version, `(dataset_version_id, training_image_id,
   split)` for every member.
8. **Upload each original** to the bucket under exactly its `original_uri`, with
   no extension added.

Then continue from step 4. Every value must be copied exactly as the manifest
spells it: floats and timestamps are compared byte for byte in step 6.
