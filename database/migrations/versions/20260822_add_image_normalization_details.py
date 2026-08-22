"""add the normalization transform to images

`images` has carried `normalized_uri`, `width` and `height` since the §11
transcription, deliberately nullable so that the stage which writes them would
need no migration. It does not. What it does need is somewhere to put the
transform.

Spec §51's post-V1 defect visualisation draws bounding boxes on the *original*
photograph, and the artifact every model measures is a warped, cropped,
resampled copy of it. Without the projective transform that produced the
artifact, a box in the artifact's coordinates cannot be put back where it came
from — and the transform is not recoverable after the fact, because the
quadrilateral it was built from is not persisted anywhere. Recording it is
therefore the difference between that feature being possible later and being
impossible.

`normalization_details` is a column beyond §11's list, taken on the reasoning
the quality-details revision gives for its own: the specification lists what a
table is *about*, and a requirement in another section needs somewhere to live.
JSONB rather than a `normalization_transforms` table because nothing joins it,
aggregates it or constrains it — it is read back whole by whatever reverses the
mapping — and a table whose sole purpose is to hold nine numbers per row is a
table to migrate for nothing.

Nullable and no backfill. NULL means "normalization has not run", which is true
of every row that exists when this is applied, and stays true afterwards for a
photograph the detector could not find a card in: there is no quadrilateral, so
there is no artifact and no transform. An empty object would claim otherwise.

Refs: M2, spec §18, §51, §11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "5c1f83a4b6d2"
down_revision: str | None = "b74c1e0a9d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "normalization_details",
            postgresql.JSONB(),
            nullable=True,
            comment=(
                "How `normalized_uri` was produced: the projective transform from the "
                "original photograph, the quarter-turn applied, the output size, the "
                "stage version and the thresholds it ran with. NULL until normalization "
                "has run — and still NULL afterwards when no card was located, because "
                "there was nothing to straighten. A column beyond §11's list, on "
                "`quality_details`'s reasoning: spec §51's post-V1 defect visualisation "
                "draws boxes on the original, and without the transform that mapping is "
                "unrecoverable. JSONB rather than a table because nothing joins it."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("images", "normalization_details")
