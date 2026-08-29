"""add the annotation schema

Spec §30 names eleven things the internal annotation application must capture,
and this revision gives all eleven a home. #153 recorded what a photograph is
and who may train on it; nothing until now recorded what is *wrong with the
card in it*, which is the label every model M7 and M8 train learns from.

The shape and the reasoning live in
`services/api/src/tcg_api/datasets/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Six things worth knowing before reading the DDL:

* **Two tables, not one with a discriminator.** §30 lists corner, edge and
  surface defect annotation *and* centering measurements, and the two are not
  the same kind of record: a marker carries a label, a severity and a bounding
  box, a measurement carries two ratios and none of those. One table would leave
  half of every row NULL by construction and would need two families of paired
  constraints to say which half was which.
* **Coordinates are fractions of the normalized artifact, never pixels of the
  photograph.** `ml/normalization` (#38) warps every image to one 756x1056
  artifact, so a coordinate in that space survives a retake and compares across
  cards. Fractions rather than pixels of that artifact, so its resolution could
  change without rewriting every row — and so `tables.py` never has to import
  the CV package, which would put OpenCV in the API image.
* **Uncertainty is NOT NULL on both tables, with no server default.** It is one
  of §30's eleven and the dataset's expression of the invariant running through
  this whole product: an annotator who cannot tell records that, and a model
  trained on their confident guess is worse than one trained on their admission.
  The other half of the same rule is the `unknown` label every one of §14, §15
  and §16's vocabularies carries.
* **§30's annotation timestamp is `created_at`, and there is no second column
  for it.** `training_images` carries both `acquired_at` and `created_at`
  because a photograph is taken long before it is ingested; an annotation
  happens when the tool writes the row.
* **Both foreign keys CASCADE**, with `training_image_fingerprints` and not with
  `dataset_members`. An annotation describing bytes nobody holds is unusable,
  and RESTRICT would make it the reason a withdrawal ADR 0008 grants could not
  be honoured; a frozen version's claim on an image is already held by
  `dataset_members`.
* **A second trigger function**, `annotation_records_are_immutable()`, rather
  than a third and fourth caller of `dataset_records_are_immutable()`. That
  function's HINT tells the reader to publish a new dataset version, which is
  the wrong instruction for an annotator who mistyped a severity. `UPDATE` only,
  as everywhere else in this domain.

No rows are inserted. `downgrade()` drops the two tables and
`annotation_records_are_immutable()` and **nothing else** — dropping
`dataset_records_are_immutable()` here would silently unguard `dataset_versions`
and `dataset_members`, which is the trap the revision below this one already
names.

Revision ID: c31f7a04b8e6
Revises: a809e54401d2
Create Date: 2026-08-29 12:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c31f7a04b8e6"
down_revision: str | None = "a809e54401d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text, ordered by byte rather than by whatever locale the server was
# initialised with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

# Spec §14's four corners, §15's four edges, and the label vocabularies of §14,
# §15 and §16 — written out as literals rather than built from
# `tcg_domain.annotation`, for the reason above. Sorted, because `tables.py`
# renders them from frozensets and sorts for determinism;
# `test_datasets_tables.py` holds the two copies to each other by value.
#
# **The trailing `IS TRUE` is load-bearing**, exactly as it is on
# `ck_training_images_provenance_permits_training`: a CHECK whose expression
# evaluates to NULL **passes**, `region` is nullable, and a corner annotation
# naming no region makes `region IN (…)` NULL rather than false. Without it the
# disjunction is `NULL OR false OR false` and the row is admitted.
KIND_REGION_AND_LABEL = (
    "((kind = 'corner' "
    "AND region IN ('bottom_left', 'bottom_right', 'top_left', 'top_right') "
    "AND label IN ('chipping', 'clean', 'crease', 'dent', 'layering', 'rounding', "
    "'unknown', 'whitening')) "
    "OR (kind = 'edge' "
    "AND region IN ('bottom', 'left', 'right', 'top') "
    "AND label IN ('chipping', 'clean', 'dent', 'layering', 'notching', 'rough_cut', "
    "'unknown', 'whitening')) "
    "OR (kind = 'surface' "
    "AND region IS NULL "
    "AND label IN ('color_issue', 'dent', 'factory_defect', 'gloss_issue', 'indentation', "
    "'print_dot', 'print_line', 'registration_issue', 'scratch', 'scuff', 'stain', 'unknown'))"
    ") IS TRUE"
)

# §17 requires a severity beside every defect, and the two labels that assert no
# defect have nothing to rate. An equality between two booleans rather than two
# implications, so neither direction can be relaxed on its own.
SEVERITY_PAIRING = "(label IN ('clean', 'unknown')) = (severity IS NULL)"

# The bounding box lives in the unit square, and has area. Paired with the
# `num_nulls` constraint beside it, which is what makes "all four or none" true.
BOX_LIES_INSIDE_THE_ARTIFACT = (
    "bbox_x IS NULL OR (bbox_x >= 0 AND bbox_width > 0 AND bbox_x + bbox_width <= 1 "
    "AND bbox_y >= 0 AND bbox_height > 0 AND bbox_y + bbox_height <= 1)"
)

# Spec §53's restraint made structural: no '@' is in the grammar, so a name or
# an email address is not storable rather than merely discouraged.
ANNOTATOR_ID_PATTERN = "^[a-z0-9][a-z0-9_-]*$"

UNIT_INTERVAL = "confidence >= 0 AND confidence <= 1"

# Both centering axes, each admitting the NULL that says the axis has no
# measurable border — §21's full-art and borderless layouts.
RATIOS_ARE_UNIT_INTERVALS = (
    "(horizontal IS NULL OR (horizontal >= 0 AND horizontal <= 1)) "
    "AND (vertical IS NULL OR (vertical >= 0 AND vertical <= 1))"
)


def upgrade() -> None:
    op.create_table(
        "image_annotations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "training_image_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "The photograph this is an annotation of. **CASCADE, with "
                "`training_image_fingerprints` and not with `dataset_members`**: an "
                "annotation describing bytes nobody holds any more is unusable, and RESTRICT "
                "would make it the reason a withdrawal ADR 0008 grants could not be honoured. "
                "A frozen version's claim on an image is already held by `dataset_members`."
            ),
        ),
        sa.Column(
            "kind",
            PRINTED,
            nullable=False,
            comment=(
                "Which of spec §30's three marker features this is — corner, edge or surface "
                "defect annotation. Not 'centering': §30's fourth is a measurement rather "
                "than a marker and lives in `centering_measurements`, which shares none of "
                "the columns below. No CHECK of its own, because "
                "ck_image_annotations_kind_region_and_label_agree is a disjunction over the kinds "
                "and closes this list as a side effect of closing the other two."
            ),
        ),
        sa.Column(
            "region",
            PRINTED,
            nullable=True,
            comment=(
                "Where on the card — one of §14's four corners or one of §15's four edges. "
                "**NULL exactly when `kind` is 'surface'**, because §16 names no positions: a "
                "surface defect's position is its bounding box. The side is deliberately not "
                "here; `training_images.side` already knows which face this is, and naming it "
                "twice would let the two disagree."
            ),
        ),
        sa.Column(
            "label",
            PRINTED,
            nullable=False,
            comment=(
                "What was found — §14's eight for a corner, §15's eight for an edge, §16's "
                "twelve for a surface. The three lists differ on purpose and are not one "
                "list: 'rough_cut' is a cutting defect an edge has and a corner has not, and "
                "§16 carries no 'clean' at all, because a surface with nothing wrong is a "
                "surface nobody annotated."
            ),
        ),
        sa.Column(
            "severity",
            PRINTED,
            nullable=True,
            comment=(
                "§17's severity — minor, moderate or severe. An **ordinal rather than a "
                "number in [0, 1]**: there is one annotator and no agreement study, so finer "
                "granularity would record a precision nobody could reproduce. NULL exactly "
                "when the label asserts no defect ('clean' found nothing to rate, 'unknown' "
                "could not rate what it found), and required otherwise."
            ),
        ),
        sa.Column(
            "confidence",
            sa.Double(),
            nullable=False,
            comment=(
                "§30's uncertainty, in [0, 1] — how sure the annotator is of this call. "
                "**NOT NULL and no server default**, which is what makes it one of §30's "
                "eleven rather than a nullable afterthought: an annotator who cannot tell "
                "whether a corner is soft records that, and a model trained on their "
                "confident guess is worse than one trained on their admission. The other half "
                "of the same rule is the 'unknown' label every vocabulary carries. "
                "`market_observations.confidence`'s shape, for the reason it gives: a "
                "mandatory field in an untyped bag quietly becomes optional."
            ),
        ),
        sa.Column(
            "bbox_x",
            sa.Double(),
            nullable=True,
            comment=(
                "§17's bounding box, as a **fraction of the normalized artifact** rather than "
                "a pixel of the photograph. `ml/normalization` (#38) warps every image to one "
                "756x1056 artifact, so a coordinate in that space survives a retake and "
                "compares across cards, where a raw-photograph coordinate would be "
                "meaningless the moment the framing changed. A fraction rather than a pixel "
                "of that artifact, so its resolution could change without rewriting every row "
                "— and so this module never has to import `ml/normalization`, which would put "
                "OpenCV in the API image. All four columns or none."
            ),
        ),
        sa.Column("bbox_y", sa.Double(), nullable=True, comment="As `bbox_x`, downward."),
        sa.Column("bbox_width", sa.Double(), nullable=True, comment="As `bbox_x`, and positive."),
        sa.Column("bbox_height", sa.Double(), nullable=True, comment="As `bbox_y`, and positive."),
        sa.Column(
            "polygon",
            postgresql.JSONB(),
            nullable=True,
            comment=(
                "§17's polygon — an array of [x, y] pairs in the same fractional artifact "
                "space as the box above, for a defect a rectangle describes badly. JSONB "
                "rather than a table of points, because nothing joins it and no query asks "
                "about one vertex. §17 says to capture spatial data from the beginning even "
                "though defect visualization is post-V1, and this is that: storable now, read "
                "by nothing yet."
            ),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment=(
                "§17's metadata — whatever the tool recorded that has no column of its own. "
                "Never the label, the severity or the confidence, each of which has a column "
                "and a constraint."
            ),
        ),
        sa.Column(
            "annotator_id",
            PRINTED,
            nullable=False,
            comment=(
                "§30's annotator ID — **an opaque identifier, never a name and never an "
                "email**. Spec §53's restraint applies to the people who label the corpus as "
                "much as to the people who use the product, and "
                "ck_image_annotations_annotator_id_is_opaque is that rule rather than a convention."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=(
                "**§30's annotation timestamp, and there is deliberately no second column for "
                "it.** `training_images` carries both `acquired_at` and `created_at` because "
                "a photograph is taken long before it is ingested; an annotation *happens* "
                "when the tool writes the row, so an `annotated_at` beside this one would be "
                "one fact stored twice and free to disagree with itself."
            ),
        ),
        sa.CheckConstraint(KIND_REGION_AND_LABEL, name="kind_region_and_label_agree"),
        sa.CheckConstraint(SEVERITY_PAIRING, name="a_defect_carries_a_severity"),
        sa.CheckConstraint(
            "severity IS NULL OR severity IN ('minor', 'moderate', 'severe')",
            name="severity_is_a_known_severity",
        ),
        sa.CheckConstraint(UNIT_INTERVAL, name="confidence_is_a_unit_interval"),
        sa.CheckConstraint(
            "num_nulls(bbox_x, bbox_y, bbox_width, bbox_height) IN (0, 4)",
            name="bounding_box_is_whole_or_absent",
        ),
        sa.CheckConstraint(
            BOX_LIES_INSIDE_THE_ARTIFACT, name="bounding_box_lies_inside_the_artifact"
        ),
        sa.CheckConstraint(
            "polygon IS NULL OR jsonb_typeof(polygon) = 'array'", name="polygon_is_an_array"
        ),
        sa.CheckConstraint(
            f"annotator_id ~ '{ANNOTATOR_ID_PATTERN}'", name="annotator_id_is_opaque"
        ),
        sa.ForeignKeyConstraint(
            ["training_image_id"],
            ["training_images.id"],
            name="fk_image_annotations_training_image_id_training_images",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_image_annotations"),
        comment=(
            "One marker on one photograph — spec §30's corner, edge and surface defect "
            "annotation, carrying §17's spatial data and severity. **Append-only**: "
            "trg_image_annotations_immutable refuses an UPDATE, so a corrected annotation is a new "
            "row and a dataset version that referenced the old one keeps meaning what it "
            "meant. Nothing is unique per image, for the same reason: a surface has as many "
            "defects as it has, and the current view of a corner is the newest row for it. "
            "Named `image_annotations` rather than `annotations` because every module here "
            "carries `from __future__ import annotations`, and a table object called "
            "`annotations` shadows that binding wherever it is imported — the name is also "
            "the more accurate one, since this annotates an image."
        ),
    )
    op.create_index(
        "ix_image_annotations_training_image_id", "image_annotations", ["training_image_id"]
    )

    op.create_table(
        "centering_measurements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "training_image_id",
            sa.Uuid(),
            nullable=False,
            comment="The photograph measured. CASCADE, for the reason `image_annotations` gives.",
        ),
        sa.Column(
            "horizontal",
            sa.Double(),
            nullable=True,
            comment=(
                "The left border as a fraction of the two side borders together — "
                "left / (left + right). **0.5 is perfect centering**; below it the artwork "
                "sits left, above it right. Stated in one direction here because a ratio that "
                "means two things is worse than no ratio: '55/45' is ambiguous about which "
                "number is which, and §13 asks for ratios rather than qualitative labels "
                "without saying which way round. NULL where the axis cannot be measured — see "
                "the table comment."
            ),
        ),
        sa.Column(
            "vertical",
            sa.Double(),
            nullable=True,
            comment=(
                "The top border as a fraction of the two end borders together — "
                "top / (top + bottom). 0.5 is perfect, as above."
            ),
        ),
        sa.Column(
            "confidence",
            sa.Double(),
            nullable=False,
            comment=(
                "§30's uncertainty, in [0, 1] — required here exactly as on `image_annotations`, so "
                "that every annotation type can express it. A border read off a worn or "
                "glare-lit edge is a real measurement with a low confidence, and recording it "
                "at 1.0 would be the fabricated certainty spec §2.7 forbids."
            ),
        ),
        sa.Column(
            "notes",
            sa.Text(),
            nullable=True,
            comment=(
                "Free text — in practice, which of §21's awkward layouts this card is and "
                "what the annotator measured against. Not one of §30's eleven and not a "
                "vocabulary: template awareness is M7's model, and this is the human's note "
                "to it."
            ),
        ),
        sa.Column(
            "annotator_id",
            PRINTED,
            nullable=False,
            comment="§30's annotator ID, under the same grammar `image_annotations` uses.",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="§30's annotation timestamp, as on `image_annotations` and for the same reason.",
        ),
        sa.CheckConstraint(
            "horizontal IS NOT NULL OR vertical IS NOT NULL",
            name="a_measurement_measures_something",
        ),
        sa.CheckConstraint(RATIOS_ARE_UNIT_INTERVALS, name="ratios_are_unit_intervals"),
        sa.CheckConstraint(UNIT_INTERVAL, name="confidence_is_a_unit_interval"),
        sa.CheckConstraint(
            f"annotator_id ~ '{ANNOTATOR_ID_PATTERN}'", name="annotator_id_is_opaque"
        ),
        sa.ForeignKeyConstraint(
            ["training_image_id"],
            ["training_images.id"],
            name="fk_centering_measurements_training_image_id_training_images",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_centering_measurements"),
        comment=(
            "Spec §30's centering measurements for one side of one card — §21's output, which "
            "§13 requires be ratios rather than qualitative labels. **Its own table rather "
            "than a fourth `image_annotations.kind`**: a measurement carries no label, no severity "
            "and no bounding box, and a marker carries no ratio, so one table would leave half "
            "of every row NULL by construction and would need two families of paired "
            "constraints to say which half. **Each axis is nullable on its own**, because §21 "
            "names full-art and borderless layouts outright: a card with no border on an axis "
            "has no ratio there, and inventing 0.5 for it is the confidently-wrong output §2.7 "
            "exists to forbid. Append-only, like `image_annotations`."
        ),
    )
    op.create_index(
        "ix_centering_measurements_training_image_id",
        "centering_measurements",
        ["training_image_id"],
    )

    # A second function, not a third caller of `dataset_records_are_immutable()`
    # — see the module docstring. `RAISE USING MESSAGE = ...`, concatenated,
    # because `op.execute` runs a statement asyncpg prepares and a format
    # specifier in the body would be interpolated on the way through.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION annotation_records_are_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE USING
                ERRCODE = 'restrict_violation',
                MESSAGE = TG_TABLE_NAME || ' is append-only: '
                          || TG_OP || ' was refused',
                HINT    = 'Record a correction as a new annotation rather than editing one.';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_image_annotations_immutable
        BEFORE UPDATE ON image_annotations
        FOR EACH ROW EXECUTE FUNCTION annotation_records_are_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_centering_measurements_immutable
        BEFORE UPDATE ON centering_measurements
        FOR EACH ROW EXECUTE FUNCTION annotation_records_are_immutable();
        """
    )


def downgrade() -> None:
    # `DROP TABLE` takes each trigger with it but never the function they share,
    # so the function is dropped explicitly — and it is
    # `annotation_records_are_immutable()`, never
    # `dataset_records_are_immutable()`, which still guards `dataset_versions`
    # and `dataset_members`.
    op.drop_index(
        "ix_centering_measurements_training_image_id", table_name="centering_measurements"
    )
    op.drop_table("centering_measurements")
    op.drop_index("ix_image_annotations_training_image_id", table_name="image_annotations")
    op.drop_table("image_annotations")
    op.execute("DROP FUNCTION IF EXISTS annotation_records_are_immutable()")
