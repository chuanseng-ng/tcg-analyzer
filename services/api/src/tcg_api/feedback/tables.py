"""Spec §68's feedback row, as one SQLAlchemy Core table.

The table attaches to the service-wide `MetaData` in `tcg_api.tables` and the
domain is registered in `tcg_api.table_registry` — a domain the registry does
not import is a domain `alembic revision --autogenerate` proposes dropping.

Four things about this schema are load-bearing:

* **It is outside the session cascade, and that is the whole point.** Every
  other table holding anything a user gave this product hangs off
  `analysis_sessions` and goes at seven days. A grading company takes weeks, so
  a row that expired with the session could never be answered. There is
  therefore no `session_id` and no `analysis_id` here: either would make the row
  joinable to the browser's bearer token for as long as the analysis lived, and
  `docs/retention.md`'s exemption promises it is not. What connects an analysis
  to its feedback is `analyses.feedback_minted_at`, a timestamp with nothing in
  it, on the row that expires.
* **The return code is stored as a digest and cannot be recovered.**
  `return_code_is_stored_as_a_digest` refuses anything but 64 lowercase hex, and
  `tcg_api.codes` renders a code in upper case — so the column cannot hold one
  even by mistake. Lookup is by digest, which is why the digest is unsalted: a
  per-row salt would make the only query there is impossible, and the pre-image
  is a hundred bits of uniform randomness rather than anything a dictionary
  reaches.
* **The grade CHECK is the grammar; the scale is Python's.** `grading_outcomes`'
  rule (#165) and for its reason — a per-company CHECK would make a fourth
  company cost a migration. `tcg_api.feedback.store.verify_answer` is where PSA
  learns it issues no 9.5.
* **The status walks forward along a branch, not a line.** `awaiting` →
  `submitted` → `validated` | `rejected`. See the trigger below for why
  `model_bundles`' `array_position` rule cannot express this one.

**Two triggers that `model_bundles` has are deliberately absent here.** There is
no immutability trigger over the answer: it is written by exactly one statement,
`WHERE status = 'awaiting'`, so a second answer matches no row —
`state.transition`'s argument — and the CHECK already refuses an answer on an
`awaiting` row. And there is no undeletable trigger, because the hourly sweep
must be able to delete: a registry row is the record that a version existed, and
this one is a question that expires. Copying `model_bundles` wholesale would
make `docs/retention.md` a document the schema contradicts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_economic_engine import RecommendedAction
from tcg_grading_companies import Designation, GradingCompany

from tcg_api.tables import ISSUED_GRADE_PATTERN, PRINTED, metadata, one_of

__all__ = [
    "TABLES",
    "TRANSITIONS",
    "FeedbackStatus",
    "grade_feedback",
]


class FeedbackStatus(StrEnum):
    """Where one report has got to — spec §68's validation step, as a column.

    A closed vocabulary, like `AnalysisStatus` and unlike `GradingCompany`: §68
    names user feedback, validation and an approved dataset, and a fifth value
    would be a fifth thing the review command has to mean something by.
    """

    #: Minted, and nobody has answered yet. The state every row is born in.
    AWAITING = "awaiting"
    #: The user reported a grade. The only move a route can make.
    SUBMITTED = "submitted"
    #: An operator checked the report and believes it. Terminal.
    VALIDATED = "validated"
    #: An operator checked the report and does not. Terminal, and not a
    #: judgement about the user — an unreadable certification number reaches
    #: here too.
    REJECTED = "rejected"


#: Every legal move, as pairs. **A branch, not a line**, which is why this is a
#: set of pairs rather than `model_bundles`' ordered `LIFECYCLE`: an
#: `array_position` comparison says only "later than", and `validated` and
#: `rejected` are two terminals at the same depth, so it would legalise a
#: reviewer changing a verdict in place. §68 makes validation a deliberate act,
#: and an undoable one is not that.
TRANSITIONS: Final = (
    (FeedbackStatus.AWAITING, FeedbackStatus.SUBMITTED),
    (FeedbackStatus.SUBMITTED, FeedbackStatus.VALIDATED),
    (FeedbackStatus.SUBMITTED, FeedbackStatus.REJECTED),
)

#: 64 lowercase hex characters, bare — `images.sha256`'s spelling, because the
#: column already names the algorithm.
_DIGEST_PATTERN: Final = "^[0-9a-f]{64}$"

#: Strict both ways, #265's `failure_is_recorded_exactly_when_failed`. An
#: `awaiting` row carrying an answer would be a claim its own status
#: contradicts, and an answered row missing its company is the guess these
#: columns exist to end. A designation with no grade is legal on purpose: PSA
#: issues `authentic` *in place of* a grade (#165), so requiring one would
#: refuse an honest report.
_ANSWERED: Final = (
    "(status = 'awaiting' AND num_nonnulls("
    "grading_company, grade, designation, certification_number, submitted_at) = 0) "
    "OR (status <> 'awaiting' AND grading_company IS NOT NULL "
    "AND submitted_at IS NOT NULL "
    "AND (grade IS NOT NULL OR designation IS NOT NULL))"
)


grade_feedback = sa.Table(
    "grade_feedback",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "return_code_hash",
        PRINTED,
        nullable=False,
        comment=(
            "The sha256 of the normalised return code, and the only form of it "
            "that exists anywhere. The code itself is shown once, in the body of "
            "the request that minted it, and is never returned, logged or stored "
            "— so a lost code is a lost row, deliberately. Unsalted because "
            "lookup by digest is the only query there is, and the pre-image is a "
            "hundred bits of uniform randomness rather than anything a "
            "dictionary reaches."
        ),
    ),
    sa.Column(
        "card_id",
        sa.Uuid(),
        sa.ForeignKey("cards.id", ondelete="RESTRICT", name="fk_grade_feedback_card_id_cards"),
        nullable=False,
        comment=(
            "Which printed card the analysis confirmed. NOT NULL because an "
            "analysis reaches 'completed' only through 'analyzing', which only "
            "confirm-card can enter and which writes the card in the same "
            "transaction. RESTRICT rather than CASCADE: nothing deletes from the "
            "catalog (#27), and a feedback row whose card had been set null "
            "would be a prediction about nothing."
        ),
    ),
    sa.Column(
        "predictions",
        postgresql.JSONB(),
        nullable=False,
        comment=(
            "analyses.grade_predictions copied whole at mint time — the version "
            "and thresholds as well as the per-company distributions, because a "
            "snapshot missing what dated it is one nobody can read in six "
            "months. Copied rather than referenced because the source row is "
            "swept at seven days and this one lives a hundred and eighty. That "
            "the copy is faithful is the mint statement's guarantee: no CHECK "
            "can compare it against another table, and by the time anyone asks, "
            "the analysis is gone."
        ),
    ),
    sa.Column(
        "model_bundle_version",
        PRINTED,
        nullable=True,
        comment=(
            "Spec §57's model bundle version, copied from the analysis. Nullable "
            "mirroring its source: an analysis no run has claimed has none, and "
            "inventing one would make the snapshot lie about what produced it."
        ),
    ),
    sa.Column(
        "grading_rules_version",
        PRINTED,
        nullable=True,
        comment=(
            "Spec §57's grading rules version, copied from the analysis. Nullable "
            "for its source's reason: NULL where some company had no standard "
            "recorded as in force, because a partial composite would misreport "
            "which rules the prediction was made under."
        ),
    ),
    sa.Column(
        "recommended_action",
        sa.Text(),
        nullable=True,
        comment=(
            "Spec §44's verdict as the user was shown it, so that spec §26 can "
            "later ask whether the advice paid off. NULL where the results "
            "screen showed no verdict at all — no assessed photograph, §44's "
            "third confidence source — which is a different thing from "
            "'insufficient_information', the verdict that declines out loud."
        ),
    ),
    sa.Column(
        "status",
        sa.Text(),
        nullable=False,
        comment=(
            "Spec §68's validation step. Every row is born 'awaiting' and the "
            "mint writes that explicitly rather than leaning on a server "
            "default, `model_bundles`' rule: a default is a state nobody chose. "
            "'validated' and 'rejected' are the review command's alone — no "
            "route writes either."
        ),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "expires_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        comment=(
            "When the hourly sweep may delete this row. No server default, "
            "`analysis_sessions.expires_at`'s rule: the period is policy and "
            "belongs where a reviewer reads it, which is "
            "TCG_API_FEEDBACK_TTL_SECONDS and docs/retention.md."
        ),
    ),
    sa.Column(
        "grading_company",
        sa.Text(),
        nullable=True,
        comment="Which company issued the grade the user reports. NULL until answered.",
    ),
    sa.Column(
        "grade",
        PRINTED,
        nullable=True,
        comment=(
            "The grade the user says they received, as `tcg_domain.Grade` renders "
            "one point. The CHECK is the grammar and the per-company scale is "
            "Python's (#165) — PSA issues no 9.5 and BGS does, and saying so here "
            "would make a fourth company cost a migration. NULL until answered, "
            "and NULL beside a designation for a slab that carries one in place "
            "of a grade."
        ),
    ),
    sa.Column(
        "designation",
        sa.Text(),
        nullable=True,
        comment=(
            "A designation the slab carries — PSA's 'authentic' in place of a "
            "grade, BGS's 'black_label' on top of a 10. Never a value on a grade "
            "scale. NULL until answered, and NULL for most answers."
        ),
    ),
    sa.Column(
        "certification_number",
        PRINTED,
        nullable=True,
        comment=(
            "The number printed on the slab, if the user has it to hand. "
            "Optional, unlike `grading_outcomes`' — that row is an operator "
            "transcribing from a slab in front of them, and this one is a person "
            "answering a question weeks later."
        ),
    ),
    sa.Column(
        "submitted_at",
        sa.TIMESTAMP(timezone=True),
        nullable=True,
        comment="When the user answered. NULL exactly while the status is 'awaiting'.",
    ),
    sa.CheckConstraint(_ANSWERED, name="answer_is_recorded_exactly_when_answered"),
    sa.CheckConstraint(
        "certification_number IS NULL OR btrim(certification_number) <> ''",
        name="certification_number_is_not_blank",
    ),
    sa.CheckConstraint(
        f"designation IS NULL OR {one_of('designation', Designation)}",
        name="designation_is_a_known_designation",
    ),
    sa.CheckConstraint("expires_at > created_at", name="expires_after_it_was_created"),
    sa.CheckConstraint(
        f"grade IS NULL OR grade ~ '{ISSUED_GRADE_PATTERN}'",
        name="grade_is_an_issued_grade",
    ),
    sa.CheckConstraint(
        f"grading_company IS NULL OR {one_of('grading_company', GradingCompany)}",
        name="grading_company_is_supported",
    ),
    # The shape, and no deeper. A CHECK cannot say "this matches the analysis it
    # was copied from" — it may not reference another table, and by the time
    # anyone asks, that analysis is gone.
    sa.CheckConstraint("jsonb_typeof(predictions) = 'object'", name="predictions_is_a_document"),
    sa.CheckConstraint(
        f"recommended_action IS NULL OR {one_of('recommended_action', RecommendedAction)}",
        name="recommended_action_is_a_known_action",
    ),
    sa.CheckConstraint(
        f"return_code_hash ~ '{_DIGEST_PATTERN}'",
        name="return_code_is_stored_as_a_digest",
    ),
    sa.CheckConstraint(one_of("status", FeedbackStatus), name="status_is_a_known_status"),
    sa.UniqueConstraint("return_code_hash", name="uq_grade_feedback_return_code_hash"),
    # The sweep's query, `ix_analysis_sessions_expires_at`'s counterpart. No
    # index on `status`: the review list is a handful of rows in a table bounded
    # by a hundred and eighty days of opt-ins. No index on `card_id`: nothing
    # deletes from the catalog, so the RESTRICT check never runs in anger.
    sa.Index("ix_grade_feedback_expires_at", "expires_at"),
    comment=(
        "One report of the grade a card actually received — spec §68. Outside "
        "the session cascade and expiring on its own clock; see "
        "docs/retention.md, which justified that before this table existed."
    ),
)


def _ddl(statement: str) -> sa.DDL:
    """`sa.DDL` is unannotated in SQLAlchemy's own types, and mypy runs strict here."""
    return sa.DDL(statement)  # type: ignore[no-untyped-call]


#: The legal moves, rendered into the trigger's condition.
_LEGAL: Final = "\n            OR ".join(
    f"(OLD.status = '{before}' AND NEW.status = '{after}')" for before, after in TRANSITIONS
)

# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: `sa.DDL` runs its statement through Python's `%` interpolation, so a
# format specifier in the body fails at compile time. Do not "simplify" it back.
_STATUS_FUNCTION: Final = _ddl(
    f"""
    CREATE OR REPLACE FUNCTION grade_feedback_status_walks_forward()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NOT (
            {_LEGAL}
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
)

_STATUS_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_grade_feedback_status_walks_forward
    BEFORE UPDATE ON grade_feedback
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION grade_feedback_status_walks_forward();
    """
)

# One `sa.DDL` per DROP: asyncpg prepares each statement, and a prepared
# statement cannot hold two. The function goes last, after the trigger naming it.
_DROP_STATUS_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_grade_feedback_status_walks_forward ON grade_feedback"
)
_DROP_STATUS_FUNCTION: Final = _ddl("DROP FUNCTION IF EXISTS grade_feedback_status_walks_forward()")

sa.event.listen(grade_feedback, "after_create", _STATUS_FUNCTION.execute_if(dialect="postgresql"))
sa.event.listen(grade_feedback, "after_create", _STATUS_TRIGGER.execute_if(dialect="postgresql"))
sa.event.listen(
    grade_feedback, "before_drop", _DROP_STATUS_TRIGGER.execute_if(dialect="postgresql")
)
sa.event.listen(
    grade_feedback, "before_drop", _DROP_STATUS_FUNCTION.execute_if(dialect="postgresql")
)

#: Every table this domain declares. Read by `tcg_api.table_registry`.
TABLES: Final = (grade_feedback,)
