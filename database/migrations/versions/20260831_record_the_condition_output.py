"""record the condition output an analysis produced

M7's acceptance criterion is structured condition output with uncertainty for
a real analysis, and it needs somewhere to live (#187). `condition_details` is
a column beyond §12's list, on exactly `quality_details`' precedent: the
specification's schema section says what a table is *about*, and a requirement
in another section needs a home. JSONB rather than a `condition_assessments`
table on the same reasoning that revision gives — nothing joins it, aggregates
it or constrains it; M8 reads the whole document to feed the three company
models — and a table nothing queries relationally is a table to migrate for
nothing.

The second half is `quality_score`'s promise-keeping pattern applied to
`model_bundle_version`: its comment said "NULL in V1 because no model exists
yet", and #187 is the change that makes that false — the worker now writes the
composed condition version at every claim. Autogenerate compares column
comments, so the comment changes here and in `tcg_api/analysis/tables.py`
together, or the drift guard fails.

Refs: M7, spec §13, §57, #187
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c9e47d20b6"
down_revision: str | None = "f2c9a41d57e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STALE_MODEL_BUNDLE_COMMENT = (
    "The model bundle that produced the result — 'pokemon-condition-v0.3.0'. "
    "An explicit identifier, never '/latest/' (spec §31). NULL in V1 because "
    "no model exists yet: a documented absence rather than a gap."
)

_MODEL_BUNDLE_COMMENT = (
    "The model bundle the condition output was produced under — the composed "
    "condition version, e.g. 'condition-compose-v0.1.0+centering-opencv-v0.1.0+…'. "
    "An explicit identifier, never '/latest/' (spec §31), resolved when the run "
    "claimed the analysis (#187). NULL on analyses that predate the condition "
    "step: a documented absence rather than a gap."
)


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "condition_details",
            postgresql.JSONB(),
            nullable=True,
            comment=(
                "The neutral condition assessment the worker produced — spec §13's tree "
                "as a document, the composed condition version and the analyzers' "
                "thresholds beside it so a row explains itself, or a top-level "
                "insufficient_information with its reason where nothing could be "
                "assessed (#187). NULL means the condition step never ran — never that "
                "the card is clean. JSONB rather than a table on `quality_details`' "
                "reasoning: nothing joins it, and M8 reads the whole document."
            ),
        ),
    )
    # Nullable and no backfill, on purpose. NULL means "the condition step never
    # ran", which is true of every row that exists when this is applied — an
    # empty object would claim a card was assessed and found clean.

    op.alter_column(
        "analyses",
        "model_bundle_version",
        existing_type=sa.Text(collation="C"),
        existing_nullable=True,
        comment=_MODEL_BUNDLE_COMMENT,
        existing_comment=_STALE_MODEL_BUNDLE_COMMENT,
    )


def downgrade() -> None:
    op.alter_column(
        "analyses",
        "model_bundle_version",
        existing_type=sa.Text(collation="C"),
        existing_nullable=True,
        comment=_STALE_MODEL_BUNDLE_COMMENT,
    )
    op.drop_column("analyses", "condition_details")
