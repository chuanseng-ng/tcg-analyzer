"""name the representation an annotation marks

#158 fixed that an annotation's coordinates are fractions of the normalized
artifact, and #160's endpoint refuses coordinates where no artifact exists.
ADR 0010 then measured that rule's limit: at 12 px/mm a hairline scratch is
0.1-0.6 px, so §16's fine defect classes — scratch, print_line, print_dot,
gloss_issue — are below the sampling limit of the artifact at any rate a
source photograph supports. The original photograph (~34-36 px/mm as measured)
is the only frame in which those classes are even arguable, and the ADR names
#175 as the one route back: a *surface* annotation may declare its coordinates
fractions of the original photograph, as an explicit representation on the row
rather than a convention.

`representation` is that declaration. NOT NULL on every row — a marker with no
box still names the frame the annotator judged the label against, and a
'scratch' call made off the 12 px/mm artifact is a weaker claim than one made
off the original. Two CHECKs close it: the value is one of the two frames this
schema stores, and only a surface annotation may name the original — corners
and edges were measured adequate against the artifact (ADR 0010) and stay
fractions of it, as does every centering measurement, whose table gets no
representation column at all.

The backfill is the ADD COLUMN default, deliberately. Every pre-#175 row was
made against the artifact — a bounding box required one to exist — and
`trg_image_annotations_immutable` refuses the UPDATE an ordinary backfill
would need. Adding the column with a 'normalized' default rewrites the table
without firing that trigger, and the default is then dropped so a writer that
names no representation is refused rather than silently agreeing, exactly as
`confidence` refuses a certainty nobody chose.

`ck_image_annotations_bounding_box_lies_inside_the_artifact` keeps its name
and its text: the unit-square rule is the same in either frame, and renaming a
CHECK is a drop-and-re-add against a frozen revision's constant for a name
alone. The bbox and polygon comments are re-worded here because Alembic
compares column comments, and a declaration that said "fraction of the
normalized artifact" would now be claiming something false of a surface row
naming the original.

Refs: M7, #175, ADR 0010, spec §16, §17, §30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f2c9a41d57e8"
down_revision: str | None = "d4b7e1c60a29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRINTED = sa.Text(collation="C")

# The two frames a coordinate can be a fraction of. A literal snapshot of
# `tables.py`'s `one_of("representation", REPRESENTATIONS)` render, held to the
# declaration by `test_datasets_tables.py`'s generic source search.
REPRESENTATION_IS_KNOWN = "representation IN ('normalized', 'original')"

# ADR 0010: #175 changes the coordinate space of *surface* annotations only.
# Both operands are NOT NULL, so no `IS TRUE` wrap is needed here.
ONLY_A_SURFACE_MARKS_THE_ORIGINAL = "kind = 'surface' OR representation = 'normalized'"

REPRESENTATION_COMMENT = (
    "Which frame this annotation's coordinates are fractions of — 'normalized' "
    "(#38's artifact) or 'original' (the photograph as ingested). ADR 0010 "
    "measured that the artifact cannot resolve §16's fine defect classes and "
    "named the original photograph the one route back, so #175 lets a *surface* "
    "annotation declare it — and only a surface, which "
    "ck_image_annotations_only_a_surface_marks_the_original holds. **NOT NULL on "
    "every row**: a marker with no box still names the frame the annotator "
    "judged the label against, and a 'scratch' call made off the 12 px/mm "
    "artifact is a weaker claim than one made off the original. No server "
    "default, for `confidence`'s reason: a representation nobody named must be "
    "refused rather than read as a choice."
)

BBOX_X_COMMENT = (
    "§17's bounding box, as a **fraction of the representation this row names** "
    "rather than a pixel of it. For 'normalized' that is the 756x1056 artifact "
    "`ml/normalization` (#38) warps every image to, so the coordinate survives a "
    "retake and compares across cards; for 'original' it is the photograph "
    "itself, which ADR 0010 measured as the only frame that resolves §16's fine "
    "defect classes. A fraction rather than a pixel of either, so a resolution "
    "can change without rewriting every row — and so this module never has to "
    "import `ml/normalization`, which would put OpenCV in the API image. All four "
    "columns or none."
)

BBOX_X_COMMENT_BEFORE = (
    "§17's bounding box, as a **fraction of the normalized artifact** rather than "
    "a pixel of the photograph. `ml/normalization` (#38) warps every image to one "
    "756x1056 artifact, so a coordinate in that space survives a retake and "
    "compares across cards, where a raw-photograph coordinate would be "
    "meaningless the moment the framing changed. A fraction rather than a pixel "
    "of that artifact, so its resolution could change without rewriting every row "
    "— and so this module never has to import `ml/normalization`, which would put "
    "OpenCV in the API image. All four columns or none."
)

POLYGON_COMMENT = (
    "§17's polygon — an array of [x, y] pairs in the same fractional space as "
    "the box above (the representation this row names), for a defect a rectangle "
    "describes badly. JSONB rather than a table of points, because nothing joins "
    "it and no query asks about one vertex. §17 says to capture spatial data "
    "from the beginning even though defect visualization is post-V1, and this is "
    "that: storable now, read by nothing yet."
)

POLYGON_COMMENT_BEFORE = (
    "§17's polygon — an array of [x, y] pairs in the same fractional artifact "
    "space as the box above, for a defect a rectangle describes badly. JSONB "
    "rather than a table of points, because nothing joins it and no query asks "
    "about one vertex. §17 says to capture spatial data from the beginning even "
    "though defect visualization is post-V1, and this is that: storable now, read "
    "by nothing yet."
)


def upgrade() -> None:
    # The default is the backfill: ADD COLUMN rewrites every existing row as
    # 'normalized' — true of all of them, since a box required the artifact —
    # without firing the immutability trigger an UPDATE would.
    op.add_column(
        "image_annotations",
        sa.Column(
            "representation",
            PRINTED,
            nullable=False,
            server_default="normalized",
            comment=REPRESENTATION_COMMENT,
        ),
    )
    # And then it goes, so no writer can leave the choice to silence.
    op.alter_column(
        "image_annotations",
        "representation",
        existing_type=PRINTED,
        existing_nullable=False,
        server_default=None,
        existing_comment=REPRESENTATION_COMMENT,
    )

    # The short names — Alembic applies `target_metadata`'s naming convention
    # itself; the image-quality-details revision records why.
    op.create_check_constraint(
        "representation_is_a_known_representation",
        "image_annotations",
        REPRESENTATION_IS_KNOWN,
    )
    op.create_check_constraint(
        "only_a_surface_marks_the_original",
        "image_annotations",
        ONLY_A_SURFACE_MARKS_THE_ORIGINAL,
    )

    op.alter_column(
        "image_annotations",
        "bbox_x",
        existing_type=sa.Double(),
        existing_nullable=True,
        comment=BBOX_X_COMMENT,
        existing_comment=BBOX_X_COMMENT_BEFORE,
    )
    op.alter_column(
        "image_annotations",
        "polygon",
        existing_type=postgresql.JSONB(),
        existing_nullable=True,
        comment=POLYGON_COMMENT,
        existing_comment=POLYGON_COMMENT_BEFORE,
    )


def downgrade() -> None:
    op.alter_column(
        "image_annotations",
        "polygon",
        existing_type=postgresql.JSONB(),
        existing_nullable=True,
        comment=POLYGON_COMMENT_BEFORE,
        existing_comment=POLYGON_COMMENT,
    )
    op.alter_column(
        "image_annotations",
        "bbox_x",
        existing_type=sa.Double(),
        existing_nullable=True,
        comment=BBOX_X_COMMENT_BEFORE,
        existing_comment=BBOX_X_COMMENT,
    )
    op.drop_constraint("only_a_surface_marks_the_original", "image_annotations", type_="check")
    op.drop_constraint(
        "representation_is_a_known_representation", "image_annotations", type_="check"
    )
    op.drop_column("image_annotations", "representation")
