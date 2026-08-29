"""store the normalized training artifact

Spec §30's annotation tool shows an image and §21's centering is measured on
it, and #158 fixed what those measurements are *of*: fractions in `[0, 1]` of
the standardized 756x1056 artifact, never pixels of a photograph. Nothing
stored that artifact. `training_images` carried `original_uri` and nothing
else, and the near-duplicate pass recomputed the artifact in memory per run and
threw it away.

That gap is not cosmetic. An annotator working against a raw photograph places
a corner at a fraction of *that frame*, which after a retake — or beside any
other photograph of the same card — means a different place on the card. The
artifact is what makes a coordinate comparable, so it has to be an object with
a key before the tool that produces coordinates is worth building.

Producing one on demand is not available: it needs card detection and
normalization, therefore OpenCV, and `tests/test_import_purity.py` holds the CV
stack out of the API image. So the artifact is produced out of band by
`tcg-normalize-training-images` from the worker image, exactly as the
near-duplicate fingerprints are, and this revision is where it is recorded.

`normalization_details` is JSONB on `images.normalization_details`'s reasoning
and in its shape — `Normalized.as_record()`, written whole and read back whole.
It carries the artifact's own width and height, which is why no
`normalized_width`/`normalized_height` pair appears here: `training_images.width`
and `height` are the *photograph's* and are NOT NULL, and overloading them the
way `images` does would destroy a fact this table needs.

It records what produced an artifact. It is deliberately **not** a staleness
check: the pass selects rows where `normalized_uri IS NULL` and nothing else,
because re-warping an image somebody has already annotated would move every
stored fraction without touching a row in `image_annotations`. A version bump
is a deliberate act with a re-annotation behind it, not a re-run.

Both columns are nullable with no backfill and no CHECK pairing them. NULL
means "the pass has not run", which is true of every row that exists when this
is applied — and stays true afterwards for a photograph the detector found no
card in, which is a row the annotation tool renders from `original_uri` while
saying so.

Refs: M6, #159, spec §18, §21, §30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d4b7e1c60a29"
down_revision: str | None = "c31f7a04b8e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "training_images",
        sa.Column(
            "normalized_uri",
            sa.Text(),
            nullable=True,
            comment=(
                "A server-generated storage key for the perspective-corrected, cropped "
                "artifact spec §30's annotation tool shows and §21's coordinates are "
                "fractions of. NULL until `tcg-normalize-training-images` has run, and "
                "still NULL afterwards where no card was located — there was nothing to "
                "straighten. `images.normalized_uri`'s meaning, on the corpus."
            ),
        ),
    )
    op.add_column(
        "training_images",
        sa.Column(
            "normalization_details",
            postgresql.JSONB(),
            nullable=True,
            comment=(
                "How `normalized_uri` was produced: the projective transform from the "
                "photograph, the quarter-turn applied, the artifact's size, the stage "
                "version and the thresholds it ran with — `Normalized.as_record()`, the "
                "shape `images.normalization_details` already carries. The artifact's size "
                "lives here rather than in two columns beside `width` and `height`, "
                "because those two are the *photograph's* and must keep meaning that. "
                "It records what produced an artifact; it is deliberately **not** a "
                "staleness check, because §30's coordinates are fractions of the "
                "artifact an annotator actually saw and re-warping one under a bumped "
                "version would move every stored fraction without touching a row."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("training_images", "normalization_details")
    op.drop_column("training_images", "normalized_uri")
