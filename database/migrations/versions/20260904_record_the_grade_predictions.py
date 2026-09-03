"""record the grade predictions an analysis produced

M8's product criterion is a grade distribution per company for a real
analysis, kept in full with the versions that produced it, and it needs
somewhere to live (#227). `grade_predictions` is `condition_details`' sibling
and one stage downstream of it, on that revision's reasoning: nothing joins
it, aggregates it or constrains it, the results route reads the whole document
and renders per company from it, and a table nothing queries relationally is
a table to migrate for nothing. One document holds all three companies because
the worker predicts for all three at the claim — no economic configuration
exists yet to select among them, so nothing is selected.

The second half is `quality_score`'s promise-keeping pattern, twice.
`grading_rules_version`'s comment said "Always NULL in V1: no grading rules
exist yet", which stopped being true the moment `tcg-seed-grading-rules` ran
and becomes actively wrong here — the worker now records the standards in
force at every claim. `model_bundle_version`'s named the condition version
alone; ADR 0011 composes the three per-company grading versions into it.
Autogenerate compares column comments, so each changes here and in
`tcg_api/analysis/tables.py` together, or the drift guard fails.

Refs: M8, spec §57, §63, #227, ADR 0011
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d81e2f9a37"
down_revision: str | None = "b7e40d2a6c15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRINTED = sa.Text(collation="C")

_STALE_MODEL_BUNDLE_COMMENT = (
    "The model bundle the condition output was produced under — the composed "
    "condition version, e.g. "
    "'condition-compose-v0.1.0+centering-opencv-v0.1.0+…'. An explicit "
    "identifier, never '/latest/' (spec §31), resolved when the run claimed the "
    "analysis (#187). NULL on analyses that predate the condition step: a "
    "documented absence rather than a gap."
)

_MODEL_BUNDLE_COMMENT = (
    "The model bundle the condition output and the grade predictions were "
    "produced under — the composed condition version joined to the three "
    "per-company grading versions, e.g. "
    "'condition-compose-v0.1.0+…+grading-bgs-heuristic-v0.1.0+grading-psa-heuristic-v0.1.0+…' "
    "(#187, #227, ADR 0011). An explicit identifier, never '/latest/' (spec §31), "
    "resolved when the run claimed the analysis. NULL on analyses that predate "
    "the condition step, and the bare condition version on those that predate the "
    "grading step: a documented absence rather than a gap."
)

_STALE_GRADING_RULES_COMMENT = (
    "Which grading-rule version the prediction was made under — spec §57. Always "
    "NULL in V1: no grading rules exist yet, and the column is here so the "
    "absence is documented rather than indistinguishable from a bug when they do. "
    "The milestone that introduces them fills it at run time."
)

_GRADING_RULES_COMMENT = (
    "Which published grading standards were in force when the analysis ran — spec "
    "§57, resolved when the run claimed it (#227, ADR 0011). One string for three "
    "companies: their grading_rules.version identifiers joined with '+' in slug "
    "order, because at the claim no company has been selected and all three "
    "standards were in force. What was in force, not what was consulted — a V1 "
    "predictor reads no machine-readable rules. NULL when no run has claimed the "
    "analysis, or when some company had no standard recorded as in force: a "
    "partial composite would misreport."
)

_GRADE_PREDICTIONS_COMMENT = (
    "What each grading company's model concluded from the condition assessment — "
    "per company slug, the full grade distribution, the model's confidence and "
    "its version, or a one-key insufficient_information with its reason; the "
    "composed grading version and the predictors' thresholds beside them so a row "
    "explains itself (#227). NULL means the grading step never ran — a refusal is "
    "a stored value, never an absence. JSONB on `condition_details`' reasoning: "
    "nothing joins it, and the results route reads the whole document."
)


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "grade_predictions",
            postgresql.JSONB(),
            nullable=True,
            comment=_GRADE_PREDICTIONS_COMMENT,
        ),
    )
    # Nullable and no backfill, on purpose. NULL means "the grading step never
    # ran", which is true of every row that exists when this is applied — and
    # #187's rule holds: an analysis records the versions in force when it ran,
    # so nothing is re-predicted.

    op.alter_column(
        "analyses",
        "model_bundle_version",
        existing_type=_PRINTED,
        existing_nullable=True,
        comment=_MODEL_BUNDLE_COMMENT,
        existing_comment=_STALE_MODEL_BUNDLE_COMMENT,
    )
    op.alter_column(
        "analyses",
        "grading_rules_version",
        existing_type=_PRINTED,
        existing_nullable=True,
        comment=_GRADING_RULES_COMMENT,
        existing_comment=_STALE_GRADING_RULES_COMMENT,
    )


def downgrade() -> None:
    op.alter_column(
        "analyses",
        "grading_rules_version",
        existing_type=_PRINTED,
        existing_nullable=True,
        comment=_STALE_GRADING_RULES_COMMENT,
        existing_comment=_GRADING_RULES_COMMENT,
    )
    op.alter_column(
        "analyses",
        "model_bundle_version",
        existing_type=_PRINTED,
        existing_nullable=True,
        comment=_STALE_MODEL_BUNDLE_COMMENT,
        existing_comment=_MODEL_BUNDLE_COMMENT,
    )
    op.drop_column("analyses", "grade_predictions")
