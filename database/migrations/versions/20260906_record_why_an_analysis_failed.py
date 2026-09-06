"""record why an analysis failed

Until now `failed` was the only fact a failed analysis carried. `_fail` in
`tcg_api.analysis.jobs` wrote the status and nothing else, and every consumer
that needed a reason guessed one from `images.quality_status` — right for the
one non-exception failure (spec §19's gate refusing) and wrong for every other:
a catalog that was down, a model that raised, a job the runner gave up on. On
the wire those looked like a healthy run whose photographs passed (#265).

Two nullable columns beside `status`: spec §66's code, which is only ever
`image_quality_failure` or `analysis_failed` because the taxonomy is closed at
eight (ADR 0005), and the reason, a closed vocabulary that lives in
`tcg_api.analysis.failures` and nowhere else. Never a message, a traceback or
exception text — spec §54, and rule 3 of the job module's docstring, applied
to the row. The CHECK is strict both ways: a `failed` row carries both, and no
other row carries either. The pair is written in the same statement as the
move to `failed`, so the constraint never sees them apart.

The backfill is the price of the strict CHECK. Rows that were `failed` before
this revision have no reason recorded, so it is derived once, here, from the
photographs — the last time that derivation is ever made: a row with an
`unusable` image is `unusable_photograph`, and any other is
`job_dead_lettered`, the honest bucket for a runner that gave up without saying
why. Neither UPDATE touches a column the reproducibility trigger guards.

Refs: M10, spec §54, §66, #265, ADR 0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a3c5d9b1f2"
down_revision: str | None = "c4d81e2f9a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FAILURE_CODE_COMMENT = (
    "Which of spec §66's codes a failed analysis failed under — only ever "
    "'image_quality_failure' (the §19 gate refused a photograph, the one "
    "failure the user can fix) or 'analysis_failed' (everything else); the "
    "taxonomy stays closed at eight (ADR 0005). Written in the same statement "
    "as the move to 'failed' and derived from failure_reason by the one writer "
    "(#265), so the two never disagree. NULL on every analysis that has not "
    "failed."
)

_FAILURE_REASON_COMMENT = (
    "Why a failed analysis failed — the closed vocabulary in "
    "tcg_api.analysis.failures, never a message, a traceback or exception "
    "text (spec §54, #265). Chosen from the exception's type where the job "
    "runner gives up, or 'unusable_photograph' where the gate refuses; a "
    "model that declined is a stored refusal in grade_predictions and never "
    "a failure. Read by every consumer and re-derived from the photographs by "
    "none. NULL on every analysis that has not failed."
)

# The vocabularies as literals, as every migration in this history writes them
# — a snapshot of what was applied, not a reference to a module that may move.
# Alembic compares a CHECK's name and not its text, so
# `test_analysis_schema.py`'s "every reason inserts" test is what keeps this
# list and `FailureReason` agreeing.
FAILURE_CODES = "'image_quality_failure', 'analysis_failed'"
FAILURE_REASONS = (
    "'unusable_photograph', 'catalog_unavailable', 'grading_rules_unavailable', "
    "'image_store_unavailable', 'model_failed', 'job_dead_lettered', 'timed_out', "
    "'stalled'"
)


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("failure_code", sa.Text(), nullable=True, comment=_FAILURE_CODE_COMMENT),
    )
    op.add_column(
        "analyses",
        sa.Column("failure_reason", sa.Text(), nullable=True, comment=_FAILURE_REASON_COMMENT),
    )

    # The one-time backfill, before the CHECK that would otherwise refuse to be
    # created over the rows it describes. See the module docstring.
    op.execute(
        """
        UPDATE analyses
           SET failure_code = 'image_quality_failure',
               failure_reason = 'unusable_photograph'
         WHERE status = 'failed'
           AND EXISTS (
                SELECT 1 FROM images
                 WHERE images.analysis_id = analyses.id
                   AND images.quality_status = 'unusable'
           )
        """
    )
    op.execute(
        """
        UPDATE analyses
           SET failure_code = 'analysis_failed',
               failure_reason = 'job_dead_lettered'
         WHERE status = 'failed' AND failure_code IS NULL
        """
    )

    # The short names, not the rendered ones — in `downgrade` as well. Alembic
    # applies `target_metadata`'s naming convention itself; see the
    # image-quality revision for how that was learned.
    op.create_check_constraint(
        "failure_is_recorded_exactly_when_failed",
        "analyses",
        "(status = 'failed' AND failure_code IS NOT NULL AND failure_reason IS NOT NULL) "
        "OR (status <> 'failed' AND failure_code IS NULL AND failure_reason IS NULL)",
    )
    op.create_check_constraint(
        "failure_names_a_known_reason",
        "analyses",
        f"(failure_code IS NULL OR failure_code IN ({FAILURE_CODES})) "
        f"AND (failure_reason IS NULL OR failure_reason IN ({FAILURE_REASONS}))",
    )


def downgrade() -> None:
    op.drop_constraint("failure_names_a_known_reason", "analyses", type_="check")
    op.drop_constraint("failure_is_recorded_exactly_when_failed", "analyses", type_="check")
    op.drop_column("analyses", "failure_reason")
    op.drop_column("analyses", "failure_code")
