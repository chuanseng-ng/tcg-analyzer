"""keep a prediction for the grade a user later reports

Spec §68 asks a user what grade their card actually received. Nothing in this
schema could hold the answer: the analysis and its `grade_predictions` are
deleted with the session at seven days, and a grading company takes weeks. So a
user who comes back with a slab had neither the identifier nor the prediction to
compare it against, and `grading_outcomes` (#165) is the operator's, keyed on
`physical_copies`, with no path from a user by design.

`grade_feedback` is that path — a prediction snapshot addressed by a return code
minted at results time. **It is outside the session cascade**, which is the one
exemption `docs/retention.md` carries and which was written there before this
revision existed. It holds no photograph, no object key, no `session_id`, no
`analysis_id` and no address: the grade distribution the models predicted, the
versions that produced it, the recommendation the user was shown, the catalog
card the analysis confirmed, and the **sha256 of a code displayed once**. It
expires on its own clock, `TCG_API_FEEDBACK_TTL_SECONDS`, a hundred and eighty
days, swept hourly beside the two sweeps already running.

`analyses.feedback_minted_at` is the whole of what connects an analysis to its
feedback, and it lives on the row that expires rather than on the one that
survives. A foreign key either way would make a prediction carrying no session
joinable to one for as long as the analysis lived, which is the property the
retention exemption promises. The mint is a conditional UPDATE on that column
being NULL, which is simultaneously the once-only rule and the double-tap race
guard. It is not a spec §57 column, so the reproducibility trigger — whose WHEN
clause names those six and only those — does not fire on it.

The status trigger is a **branch, not a line**: `awaiting` → `submitted`, then
`submitted` → `validated` or `rejected`. `model_bundles`' `array_position`
comparison says only "later than", and the two verdicts sit at the same depth,
so that rule would legalise a reviewer changing a verdict in place. §68 makes
validation a deliberate act, and an undoable one is not that.

Refs: M10, spec §54, §68, #270
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3f18c7d94ae"
down_revision: str | None = "e7a3c5d9b1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Redeclared locally, as every migration in this history redeclares what it
# needs: a migration is a snapshot of what was applied and must not change when
# a table module does.
PRINTED = sa.Text(collation="C")

# The vocabularies as literals, for the same reason.
FEEDBACK_STATUSES = "'awaiting', 'submitted', 'validated', 'rejected'"
GRADING_COMPANIES = "'psa', 'tag', 'bgs'"
DESIGNATIONS = "'authentic', 'authentic_altered', 'black_label', 'pristine_10', 'gem_mint_10'"
RECOMMENDED_ACTIONS = "'grade', 'do_not_grade', 'insufficient_information'"

ISSUED_GRADE_PATTERN = r"^(10|[0-9](\.5)?)$"
DIGEST_PATTERN = "^[0-9a-f]{64}$"

ANSWERED = (
    "(status = 'awaiting' AND num_nonnulls("
    "grading_company, grade, designation, certification_number, submitted_at) = 0) "
    "OR (status <> 'awaiting' AND grading_company IS NOT NULL "
    "AND submitted_at IS NOT NULL "
    "AND (grade IS NOT NULL OR designation IS NOT NULL))"
)

_MINTED_AT_COMMENT = (
    "When this analysis was turned into a spec §68 return code (#270), and the "
    "whole of what links the two. NULL means no code has been minted, and the "
    "mint is a conditional UPDATE on that being so, which is what makes a code "
    "mintable exactly once. Deliberately a timestamp rather than a foreign key: "
    "`grade_feedback` outlives this row by six months, and a reference either way "
    "would make a prediction that carries no session joinable to one. Nothing "
    "reads it but the mint."
)

_RETURN_CODE_COMMENT = (
    "The sha256 of the normalised return code, and the only form of it that "
    "exists anywhere. The code itself is shown once, in the body of the request "
    "that minted it, and is never returned, logged or stored — so a lost code is "
    "a lost row, deliberately. Unsalted because lookup by digest is the only "
    "query there is, and the pre-image is a hundred bits of uniform randomness "
    "rather than anything a dictionary reaches."
)

_CARD_COMMENT = (
    "Which printed card the analysis confirmed. NOT NULL because an analysis "
    "reaches 'completed' only through 'analyzing', which only confirm-card can "
    "enter and which writes the card in the same transaction. RESTRICT rather "
    "than CASCADE: nothing deletes from the catalog (#27), and a feedback row "
    "whose card had been set null would be a prediction about nothing."
)

_PREDICTIONS_COMMENT = (
    "analyses.grade_predictions copied whole at mint time — the version and "
    "thresholds as well as the per-company distributions, because a snapshot "
    "missing what dated it is one nobody can read in six months. Copied rather "
    "than referenced because the source row is swept at seven days and this one "
    "lives a hundred and eighty. That the copy is faithful is the mint "
    "statement's guarantee: no CHECK can compare it against another table, and "
    "by the time anyone asks, the analysis is gone."
)

_BUNDLE_COMMENT = (
    "Spec §57's model bundle version, copied from the analysis. Nullable "
    "mirroring its source: an analysis no run has claimed has none, and "
    "inventing one would make the snapshot lie about what produced it."
)

_RULES_COMMENT = (
    "Spec §57's grading rules version, copied from the analysis. Nullable for "
    "its source's reason: NULL where some company had no standard recorded as in "
    "force, because a partial composite would misreport which rules the "
    "prediction was made under."
)

_ACTION_COMMENT = (
    "Spec §44's verdict as the user was shown it, so that spec §26 can later ask "
    "whether the advice paid off. NULL where the results screen showed no "
    "verdict at all — no assessed photograph, §44's third confidence source — "
    "which is a different thing from 'insufficient_information', the verdict "
    "that declines out loud."
)

_STATUS_COMMENT = (
    "Spec §68's validation step. Every row is born 'awaiting' and the mint "
    "writes that explicitly rather than leaning on a server default, "
    "`model_bundles`' rule: a default is a state nobody chose. 'validated' and "
    "'rejected' are the review command's alone — no route writes either."
)

_EXPIRES_COMMENT = (
    "When the hourly sweep may delete this row. No server default, "
    "`analysis_sessions.expires_at`'s rule: the period is policy and belongs where "
    "a reviewer reads it, which is TCG_API_FEEDBACK_TTL_SECONDS and "
    "docs/retention.md."
)

_GRADE_COMMENT = (
    "The grade the user says they received, as `tcg_domain.Grade` renders one "
    "point. The CHECK is the grammar and the per-company scale is Python's "
    "(#165) — PSA issues no 9.5 and BGS does, and saying so here would make a "
    "fourth company cost a migration. NULL until answered, and NULL beside a "
    "designation for a slab that carries one in place of a grade."
)

_DESIGNATION_COMMENT = (
    "A designation the slab carries — PSA's 'authentic' in place of a grade, "
    "BGS's 'black_label' on top of a 10. Never a value on a grade scale. NULL "
    "until answered, and NULL for most answers."
)

_CERTIFICATION_COMMENT = (
    "The number printed on the slab, if the user has it to hand. Optional, "
    "unlike `grading_outcomes`' — that row is an operator transcribing from a slab "
    "in front of them, and this one is a person answering a question weeks later."
)

_TABLE_COMMENT = (
    "One report of the grade a card actually received — spec §68. Outside the "
    "session cascade and expiring on its own clock; see docs/retention.md, which "
    "justified that before this table existed."
)

_STATUS_FUNCTION = """
CREATE OR REPLACE FUNCTION grade_feedback_status_walks_forward()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT (
        (OLD.status = 'awaiting' AND NEW.status = 'submitted')
        OR (OLD.status = 'submitted' AND NEW.status = 'validated')
        OR (OLD.status = 'submitted' AND NEW.status = 'rejected')
    ) THEN
        RAISE USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'grade feedback ' || OLD.id || ' cannot move from '
                      || OLD.status || ' to ' || NEW.status,
            HINT    = 'The lifecycle is a branch: awaiting to submitted, then submitted to validated or rejected.';
    END IF;
    RETURN NEW;
END;
$$;
"""

_STATUS_TRIGGER = """
CREATE TRIGGER trg_grade_feedback_status_walks_forward
BEFORE UPDATE ON grade_feedback
FOR EACH ROW
WHEN (OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION grade_feedback_status_walks_forward();
"""


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "feedback_minted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment=_MINTED_AT_COMMENT,
        ),
    )

    op.create_table(
        "grade_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("return_code_hash", PRINTED, nullable=False, comment=_RETURN_CODE_COMMENT),
        sa.Column("card_id", sa.Uuid(), nullable=False, comment=_CARD_COMMENT),
        sa.Column("predictions", postgresql.JSONB(), nullable=False, comment=_PREDICTIONS_COMMENT),
        sa.Column("model_bundle_version", PRINTED, nullable=True, comment=_BUNDLE_COMMENT),
        sa.Column("grading_rules_version", PRINTED, nullable=True, comment=_RULES_COMMENT),
        sa.Column("recommended_action", sa.Text(), nullable=True, comment=_ACTION_COMMENT),
        sa.Column("status", sa.Text(), nullable=False, comment=_STATUS_COMMENT),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at", sa.TIMESTAMP(timezone=True), nullable=False, comment=_EXPIRES_COMMENT
        ),
        sa.Column(
            "grading_company",
            sa.Text(),
            nullable=True,
            comment="Which company issued the grade the user reports. NULL until answered.",
        ),
        sa.Column("grade", PRINTED, nullable=True, comment=_GRADE_COMMENT),
        sa.Column("designation", sa.Text(), nullable=True, comment=_DESIGNATION_COMMENT),
        sa.Column("certification_number", PRINTED, nullable=True, comment=_CERTIFICATION_COMMENT),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="When the user answered. NULL exactly while the status is 'awaiting'.",
        ),
        sa.CheckConstraint(ANSWERED, name="answer_is_recorded_exactly_when_answered"),
        sa.CheckConstraint(
            "certification_number IS NULL OR btrim(certification_number) <> ''",
            name="certification_number_is_not_blank",
        ),
        sa.CheckConstraint(
            f"designation IS NULL OR designation IN ({DESIGNATIONS})",
            name="designation_is_a_known_designation",
        ),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_it_was_created"),
        sa.CheckConstraint(
            f"grade IS NULL OR grade ~ '{ISSUED_GRADE_PATTERN}'",
            name="grade_is_an_issued_grade",
        ),
        sa.CheckConstraint(
            f"grading_company IS NULL OR grading_company IN ({GRADING_COMPANIES})",
            name="grading_company_is_supported",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(predictions) = 'object'", name="predictions_is_a_document"
        ),
        sa.CheckConstraint(
            f"recommended_action IS NULL OR recommended_action IN ({RECOMMENDED_ACTIONS})",
            name="recommended_action_is_a_known_action",
        ),
        sa.CheckConstraint(
            f"return_code_hash ~ '{DIGEST_PATTERN}'",
            name="return_code_is_stored_as_a_digest",
        ),
        sa.CheckConstraint(f"status IN ({FEEDBACK_STATUSES})", name="status_is_a_known_status"),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name="fk_grade_feedback_card_id_cards",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_grade_feedback"),
        sa.UniqueConstraint("return_code_hash", name="uq_grade_feedback_return_code_hash"),
        comment=_TABLE_COMMENT,
    )
    op.create_index("ix_grade_feedback_expires_at", "grade_feedback", ["expires_at"])

    op.execute(_STATUS_FUNCTION)
    op.execute(_STATUS_TRIGGER)


def downgrade() -> None:
    # The trigger before its function, and the function by its own name only.
    # `model_bundle_status_walks_forward()` guards `model_bundles` and is a
    # different function: dropping anything broader here would silently unguard
    # a neighbouring table.
    op.execute("DROP TRIGGER IF EXISTS trg_grade_feedback_status_walks_forward ON grade_feedback")
    op.execute("DROP FUNCTION IF EXISTS grade_feedback_status_walks_forward()")
    op.drop_index("ix_grade_feedback_expires_at", table_name="grade_feedback")
    op.drop_table("grade_feedback")
    op.drop_column("analyses", "feedback_minted_at")
