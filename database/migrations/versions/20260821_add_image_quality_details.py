"""add the image-quality gate's verdict to images

Spec §19's gate has to say *what* was wrong, not merely how bad it was: if
`unusable` the analysis stops, and if `poor` it continues but "the user must be
informed". `images` already carries `quality_score` and `quality_status` from
the §11 transcription; neither can name a condition, so neither can be turned
into the sentence a user reads.

`quality_details` is a column beyond §11's list, taken on the same reasoning
the catalog revision gives for the three it added beyond §10's: the
specification lists what a table is *about*, and a requirement in another
section needs somewhere to live. JSONB rather than a `image_quality_findings`
table because nothing joins it, aggregates it or constrains it — its only
reader renders it — and a table whose sole purpose is display copy is a table
to migrate for nothing.

The second half of this revision is a promise being kept. `quality_score` was
created deliberately unconstrained, its comment saying "the gate defines it and
may add a CHECK then". The gate now defines it: a `[0, 1]` fraction with 1 the
best. Adding the constraint here rather than later is the only cheap moment —
the column is still NULL in every row, so the ALTER cannot fail on data.

Refs: M2, spec §19, §11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b74c1e0a9d38"
down_revision: str | None = "29d14fe0fcee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "quality_details",
            postgresql.JSONB(),
            nullable=True,
            comment=(
                "What the gate concluded about each of spec §19's eleven conditions, "
                "plus the gate version and the thresholds it ran with. NULL until the "
                "gate has run. A column beyond §11's list, on the same reasoning as the "
                "three `sets`/`cards` carry beyond §10's: §19 requires the user to be "
                "told what was wrong, and a status alone cannot say. JSONB rather than a "
                "findings table because its only reader renders it."
            ),
        ),
    )
    # Nullable and no backfill, on purpose. NULL means "the gate has not run",
    # which is true of every row that exists when this is applied — an empty
    # object would claim eleven conditions were assessed and found clear.

    # The short name, not the rendered one — in `downgrade` as well. Alembic
    # applies `target_metadata`'s naming convention itself, so the prefixed form
    # produces `ck_images_ck_images_...`; `create` renders it into the DDL and
    # `drop` goes looking for a constraint under it. The card-database-version
    # revision learned the first half, and this one learned the second.
    op.create_check_constraint(
        "quality_score_is_a_unit_interval",
        "images",
        "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
    )

    # The column's own comment promised the gate would define the scale and might
    # add a CHECK then. Both halves of that promise are kept in one revision, so
    # the comment cannot outlive the state it describes.
    op.alter_column(
        "images",
        "quality_score",
        existing_type=sa.Double(),
        existing_nullable=True,
        comment=(
            "The quality gate's numeric verdict, in [0, 1] with 1 being best. "
            "NULL until the gate has run. Spec §19 fixes the four statuses and says "
            "nothing about a scale, so the gate defined this one (#36): the smallest "
            "headroom any condition had above its poor threshold, so the weakest "
            "link governs exactly as the status is the worst finding."
        ),
        existing_comment=(
            "The quality gate's numeric verdict. NULL until the gate has run. "
            "Deliberately unconstrained in range: spec §19 fixes the four statuses "
            "and says nothing about a scale, so the gate defines it and may add a "
            "CHECK then. An omission on purpose, not an oversight."
        ),
    )


def downgrade() -> None:
    op.alter_column(
        "images",
        "quality_score",
        existing_type=sa.Double(),
        existing_nullable=True,
        comment=(
            "The quality gate's numeric verdict. NULL until the gate has run. "
            "Deliberately unconstrained in range: spec §19 fixes the four statuses "
            "and says nothing about a scale, so the gate defines it and may add a "
            "CHECK then. An omission on purpose, not an oversight."
        ),
    )
    op.drop_constraint("quality_score_is_a_unit_interval", "images", type_="check")
    op.drop_column("images", "quality_details")
