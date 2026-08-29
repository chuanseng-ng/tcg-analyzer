# The annotation schema

Two tables, described here for a human. **The DDL is
[the migration](../../database/migrations/versions/20260829_add_the_annotation_schema.py)**
and the declaration is
[`services/api/src/tcg_api/datasets/tables.py`](../../services/api/src/tcg_api/datasets/tables.py);
this file is documentation, and a line here that disagrees with either of them
is stale — see [ADR 0009](../../docs/adr/0009-the-dataset-store-as-a-database-domain.md).

Spec §30 lists eleven features the internal annotation application must have.
The shape they are stored in constrains every model M7 and M8 train, which is
why it is settled here, before the tool is built against it.

## Where each of §30's eleven lives

| §30 feature | Where |
| --- | --- |
| image viewer | The tool's, not the schema's. `training_images.original_uri` is what it opens. |
| zoom | The tool's. |
| front/back | `training_images.side`, and **not repeated on the annotation** — an annotation that named a side could disagree with the image it annotates. |
| corner annotation | `image_annotations` with `kind = 'corner'` and one of §14's four `region`s. |
| edge annotation | `image_annotations` with `kind = 'edge'` and one of four `region`s. |
| surface defect annotation | `image_annotations` with `kind = 'surface'` and no `region` — §16 names no positions, so the position is the bounding box. |
| centering measurements | `centering_measurements.horizontal` and `.vertical`. |
| defect severity | `image_annotations.severity` — `minor`, `moderate`, `severe`. |
| uncertainty | `confidence` on **both** tables, NOT NULL, plus the `unknown` label every one of §14, §15 and §16's vocabularies carries. |
| annotator ID | `annotator_id` on both tables, an opaque identifier. |
| annotation timestamp | `created_at` on both tables. There is deliberately no second column — see below. |

## Two tables, and why

**A defect is a marker, a centering reading is a measurement**, and forcing both
through one row would lose the type of the second. A marker carries a label, a
severity and a bounding box; a measurement carries two ratios and none of those.
A single `annotations` table with `kind = 'centering'` would leave half of every
row NULL by construction, and would need two families of paired constraints to
say which half was meaningful — one for "this is a marker, so it has a label"
and one for "this is a measurement, so it has a ratio". Two tables mean every
column on every row means something.

What they share is what §30 asks of every annotation: an annotator, a time and
an uncertainty. Three columns repeated is the price, and they are independent
acts of annotation rather than one fact stored twice.

## `image_annotations` — one marker on one photograph

| Column | Meaning |
| --- | --- |
| `id` | |
| `training_image_id` | `ON DELETE CASCADE`. |
| `kind` | `corner`, `edge` or `surface`. |
| `region` | One of §14's four corners or four edges; `NULL` exactly when `kind` is `surface`. |
| `label` | §14's eight, §15's eight or §16's twelve, according to the kind. |
| `severity` | `minor`, `moderate` or `severe`; `NULL` exactly when the label asserts no defect. |
| `confidence` | `[0, 1]`, NOT NULL. |
| `bbox_x`, `bbox_y`, `bbox_width`, `bbox_height` | §17's bounding box, as fractions of the normalized artifact. All four or none. |
| `polygon` | §17's polygon — a JSONB array of `[x, y]` pairs in the same space. |
| `metadata` | §17's metadata. Never the label, the severity or the confidence. |
| `annotator_id` | Opaque. |
| `created_at` | §30's annotation timestamp. |

**Named `image_annotations` rather than `annotations`.** Every Python module in
this repository opens with `from __future__ import annotations`, and a table
object called `annotations` shadows that binding wherever it is imported. The
longer name is also the more accurate one: this annotates an image.

### The three vocabularies are three lists

§14, §15 and §16 are not one list with three subsets. §15 has `rough_cut` and
`notching`, which are cutting defects an edge has and a corner has not; §14 has
`rounding` and `crease`, which are not edge failures. One CHECK carries all
three, composed from `tcg_domain.annotation` so the vocabulary is stated once:

```sql
CONSTRAINT ck_image_annotations_kind_region_and_label_agree CHECK (
    (kind = 'corner'  AND region IN (…four corners…) AND label IN (…§14's eight…))
 OR (kind = 'edge'    AND region IN (…four edges…)   AND label IN (…§15's eight…))
 OR (kind = 'surface' AND region IS NULL             AND label IN (…§16's twelve…))
)
```

Because it is a disjunction over the kinds, it closes `kind` too — there is no
separate membership CHECK for it.

**§16 carries no `clean`, and §14 and §15 both do.** That asymmetry is the
specification's and is load-bearing: a corner is annotated once and may be found
sound, where a surface carries one annotation per defect found and none at all
when there are none. So "this corner is clean" is a row, and "this surface is
clean" is the absence of rows.

**Every vocabulary ends in `unknown`.** That is spec §2.7's uncertainty inside
the vocabulary rather than beside it. An annotator who can see damage but cannot
name it records where it is and says so, which is a usable training signal where
a forced guess is not.

### Severity is an ordinal, not a number

§17 requires a `severity` and defines no scale. This schema stores `minor`,
`moderate` or `severe` rather than a `[0, 1]` double, because there is one
annotator and no inter-annotator agreement study — §30's feature list has
neither — so a continuous scale would record a precision nobody could reproduce,
and a model fitting that precision would be fitting noise. M8 may map the three
to numbers; that is a modelling choice, made where the model lives.

```sql
CONSTRAINT ck_image_annotations_a_defect_carries_a_severity CHECK (
    (label IN ('clean', 'unknown')) = (severity IS NULL)
)
```

An equality between two booleans rather than two implications, so neither
direction can be relaxed on its own: `chipping` with no severity is as refused as
`clean` with one.

### Coordinates are in the normalized artifact's space

`ml/normalization` (#38) warps every image to one 756×1056 artifact. An
annotation stored against **those** coordinates survives a retake and compares
across cards; one stored against raw-photograph pixels becomes unusable the
moment the framing changes.

For a training image that artifact is a **stored object**, not something
recomputed per reader: `tcg-normalize-training-images` produces it and
`training_images.normalized_uri` names it (#159). An image that has none is one
the detector found no card in, and the annotation tool shows the photograph while
saying so — because a coordinate cannot be taken against it.

They are stored as **fractions in `[0, 1]` of that artifact** rather than as
integer pixels of it. Two reasons, and the second is the decisive one:

- the artifact's resolution could change without rewriting every row;
- `tables.py` never has to import `ml/normalization` for the two dimensions,
  which would put OpenCV in the internet-facing API image —
  `services/api/tests/test_import_purity.py` forbids exactly that.

**That first reason is about the schema, and it is not a licence to re-warp.**
A stored artifact is never replaced: an annotation is a fraction of *the artifact
its annotator saw*, so re-normalizing an image somebody has already judged would
move every coordinate on it without touching a row here. #159's pass selects
`normalized_uri IS NULL` and nothing else, and has no `--force` for that reason.
A resolution change is a deliberate act with a re-annotation behind it.

The numbers 756 and 1056 therefore appear nowhere in this schema, and a test
asserts their absence.

```sql
CONSTRAINT ck_image_annotations_bounding_box_is_whole_or_absent CHECK (
    num_nulls(bbox_x, bbox_y, bbox_width, bbox_height) IN (0, 4)
)
CONSTRAINT ck_image_annotations_bounding_box_lies_inside_the_artifact CHECK (
    bbox_x IS NULL OR (
        bbox_x >= 0 AND bbox_width  > 0 AND bbox_x + bbox_width  <= 1
    AND bbox_y >= 0 AND bbox_height > 0 AND bbox_y + bbox_height <= 1)
)
```

Three of four coordinates is not a smaller box; it is a box nobody can draw.
`polygon` is JSONB rather than a table of points, because nothing joins it and no
query asks about one vertex — §17 says to capture spatial data from the
beginning even though defect visualization is post-V1, and this is that: storable
now, read by nothing yet.

### The annotator is opaque

```sql
CONSTRAINT ck_image_annotations_annotator_id_is_opaque CHECK (
    annotator_id ~ '^[a-z0-9][a-z0-9_-]*$'
)
```

Spec §53's restraint applies to the people who label the corpus as much as to the
people who use the product. The grammar contains no `@` and no space, so a name
or an email address is **not storable** rather than merely discouraged.

## `centering_measurements` — §21's output for one side

| Column | Meaning |
| --- | --- |
| `id` | |
| `training_image_id` | `ON DELETE CASCADE`. |
| `horizontal` | left border ÷ (left + right). **`0.5` is perfect**; below it the artwork sits left. |
| `vertical` | top border ÷ (top + bottom). |
| `confidence` | `[0, 1]`, NOT NULL. |
| `notes` | Free text — in practice, which of §21's awkward layouts this card is. |
| `annotator_id` | Opaque, under the same grammar. |
| `created_at` | §30's annotation timestamp. |

**The direction is stated, and that matters.** "55/45" is ambiguous about which
number is which, and §13 asks for ratios rather than qualitative labels without
saying which way round. A ratio that means two things is worse than no ratio.

**Each axis is nullable on its own.** §21 names full-art and borderless layouts
outright: a card with no border on an axis has no ratio there, and inventing
`0.5` for it is the confidently-wrong output §2.7 exists to forbid. A row that
measures *neither* axis is refused — it would record an annotator and a time and
nothing else.

## Append-only

Both tables refuse an `UPDATE` in a trigger, as `card_database_versions` (#27)
and the market tables (#50) do. **A corrected annotation is a new row**, because
a dataset version that referenced the old reading must keep meaning what it
meant. Nothing is unique per image and per region, and that is what makes a
correction representable at all: a surface has as many defects as it has, and the
current view of a corner is the newest row for it.

`DELETE` stays open, as everywhere else in this domain — and the foreign keys
**CASCADE**, joining `training_image_fingerprints` and parting from
`dataset_members`' `RESTRICT`. An annotation describing bytes nobody holds any
more is unusable, and `RESTRICT` would make a label the reason a withdrawal
[ADR 0008](../../docs/adr/0008-permitted-training-image-sources.md) grants could
not be honoured. A frozen version's claim on an image is already held by
`dataset_members`.

The trigger function is a **second** one,
`annotation_records_are_immutable()`, rather than a third caller of
`dataset_records_are_immutable()`. Only the hint differs — that one tells the
reader to publish a new dataset version, which is the right instruction for a
frozen version and the wrong one for an annotator who mistyped a severity — and
the hint is what an operator acts on.

## §30's timestamp is `created_at`, and there is no second column

`training_images` carries both `acquired_at` and `created_at` because a
photograph is taken long before it is ingested. An annotation *happens* when the
tool writes the row, so an `annotated_at` beside `created_at` would be one fact
stored twice and free to disagree with itself.

## What is not here

**No condition representation.** The neutral `ConditionAssessment` spec §13
describes is M7's, and it is *derived from* these rows rather than being them —
`predict_grade`'s parameter is typed `object` (#37) for exactly that reason, and
M8 narrows it. Nothing here carries a grade, a condition score or a grading
company, and a test asserts their absence.

**No review workflow and no inter-annotator agreement.** §30's eleven features
include neither, and there is one annotator.

**Nothing on the public API.** These tables are read and written over
`/internal/annotation`, which is deliberately not part of spec §64 (ADR 0009):
`GET …/images` is the work list, `GET …/images/{id}` reports what has already
been recorded, and `POST …/images/{id}/annotations` writes both tables in one
transaction. The isolation is deployment topology — the `/internal` prefix is
what an ingress rule matches — and the tool is
[`apps/annotation`](../../apps/annotation/). See
[`docs/api.md`](../../docs/api.md).

**No edit path, and no `UPDATE` anywhere.** The trigger refuses one, so the
write endpoint only appends and a correction is a new row. `POST` therefore
takes no annotation identifier, and there is no `PATCH` and no `DELETE`.

**The annotator is not a request field.** §30 asks that it be recorded
automatically, so the service stamps `TCG_API_ANNOTATOR_ID` and refuses a
request that names one — which is what puts the `annotator_id` grammar out of a
client's reach entirely.
