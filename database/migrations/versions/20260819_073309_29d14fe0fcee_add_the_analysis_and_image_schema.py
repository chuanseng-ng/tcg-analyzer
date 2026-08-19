"""add the analysis and image schema

Spec §12's `analysis_sessions` and `analyses`, and §11's `images` — the spine
tying an anonymous session to its photographs, to the card the user confirmed
and eventually to a result. Nothing writes to these yet; the upload endpoint,
the state machine and the confirmation are separate issues. The shape has to
exist first, because two of the properties below cannot be retrofitted against
a table already holding user data.

The shape and the reasoning live in
`services/api/src/tcg_api/analysis/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does. The vocabularies below are literals for the same reason —
`tcg_domain.analysis` is where they live, and this is what was applied.

Five things worth knowing before reading the DDL:

* **All six `side` values exist from day one.** V1 writes `front` and `back`.
  Spec §52's guided photography adds `angled_front`, `angled_back`,
  `surface_front` and `surface_back`, and §52 states that "the V1 image pipeline
  must be compatible with this future input". Admitting them now costs a longer
  CHECK constraint; adding them later costs a migration against a table of user
  photographs. This is the issue's acceptance criterion, expressed as DDL.
* **A vocabulary is a CHECK, not a `CREATE TYPE`.** There is no `CREATE TYPE`
  anywhere in this history and this revision does not start one. PostgreSQL has
  no `ALTER TYPE ... DROP VALUE`, so a revision that added a state would not be
  reversible; `DROP TABLE` does not drop a type any more than it drops a
  trigger's function, so `downgrade` would have to name it separately, exactly
  the trap the version record's function already taught us. A named CHECK
  reverses with the table and names itself in the `IntegrityError` a caller
  sees.
* **Alembic compares a check constraint's *name*, not its *text*.** So the drift
  guard in `test_catalog_schema.py` will notice a constraint this migration
  forgot, and will **not** notice an `IN` list that has diverged from
  `tables.py`. That is what the parametrised "every value inserts" tests in
  `test_analysis_schema.py` are for; they are not decoration.
* **`expires_at` plus `ON DELETE CASCADE` is spec §54's retention mechanism.**
  Expiry is the default and retention the exception, which only has teeth if the
  column is `NOT NULL` from the first migration. The cascade then makes expiring
  a session one statement rather than a three-step job that can half-fail.
  **Cascading the rows does not delete the objects** — whoever implements the
  retention sweep must read `original_uri` and `normalized_uri` before the
  `DELETE`, or every expired photograph is orphaned in object storage forever,
  which is the privacy failure §54 describes reached through the mechanism meant
  to prevent it.
* **§12 verbatim; §57 is not pre-empted.** `card_database_version` and
  `grading_rules_version` are deliberately absent: they belong to the
  reproducibility-record issue, and must hold a published identifier resolved
  when the analysis ran rather than a pointer to "current".

No types, no functions and no triggers are created, so `downgrade()` has nothing
to drop beyond the indexes and the tables themselves. That is a consequence of
the CHECK decision above, and it is the reason not to "improve" it later.

Revision ID: 29d14fe0fcee
Revises: 352eb3d5e889
Create Date: 2026-08-19 07:33:09.404870+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "29d14fe0fcee"
down_revision: str | None = "352eb3d5e889"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Text ordered by byte rather than by whatever locale the server was initialised
# with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

# The vocabularies as they stood when this was applied. `tcg_domain.analysis` is
# the living definition; these are the snapshot, and the two are checked against
# each other by the tests rather than by Alembic.
SESSION_STATES = "'active', 'expired', 'purged'"
ANALYSIS_STATES = (
    "'created', 'uploading', 'uploaded', 'identifying', 'awaiting_confirmation', "
    "'analyzing', 'calculating', 'completed', 'failed'"
)
TERMINAL_STATES = "'completed', 'failed'"
SIDES = "'front', 'back', 'angled_front', 'angled_back', 'surface_front', 'surface_back'"
QUALITY_STATUSES = "'good', 'acceptable', 'poor', 'unusable'"


def upgrade() -> None:
    op.create_table(
        "analysis_sessions",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="Ours, and internal. What the client holds is anonymous_session_id.",
        ),
        sa.Column(
            "anonymous_session_id",
            PRINTED,
            nullable=False,
            comment=(
                "The unguessable token the client carries — spec §53. Text rather than "
                "a UUID so the generator can be chosen for entropy rather than for "
                "format; it is the only thing separating one anonymous user's "
                "photographs from another's, and it is never derived from anything "
                "about a person."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "When this session and everything hanging off it stops being kept — "
                "spec §54. No server default: the retention period is a policy that "
                "belongs where it can be reviewed, not in a column default."
            ),
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
            comment=(
                "active, expired, or purged once the retention job has deleted the "
                "images and kept the row so the deletion itself is auditable. Spec §12 "
                "names this column and never says what may go in it; these three are "
                "this project's, not the specification's."
            ),
        ),
        sa.Column(
            "application_version",
            PRINTED,
            nullable=False,
            comment=(
                "The version that opened the session, per spec §12. Spec §57 wants the "
                "same fact recorded against each analysis; that is its own issue, and "
                "this column does not stand in for it."
            ),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_sessions"),
        sa.UniqueConstraint(
            "anonymous_session_id",
            name="uq_analysis_sessions_anonymous_session_id",
        ),
        # Short names throughout: the naming convention supplies the
        # `ck_analysis_sessions_` prefix, and Alembic applies that same convention
        # to `op.create_table`, so a rendered name would render twice.
        sa.CheckConstraint(
            f"status IN ({SESSION_STATES})",
            name="status_is_a_known_session_state",
        ),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_it_was_created"),
        comment=(
            "One anonymous session — spec §12, §53. V1 has no accounts, so this is the "
            "whole of a user's continuity, and it expires."
        ),
    )
    # Serves the retention sweep — the rows now due. The unique constraint above
    # serves the only other query there is: one session, by the token presented.
    op.create_index("ix_analysis_sessions_expires_at", "analysis_sessions", ["expires_at"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "session_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "CASCADE: an analysis belongs to its session and means nothing without "
                "one, which is what lets the retention job expire a session with a "
                "single delete rather than a traversal it could get wrong."
            ),
        ),
        sa.Column(
            "card_id",
            sa.Uuid(),
            nullable=True,
            comment=(
                "Nullable on purpose: unknown until the user confirms the "
                "identification (spec §20), which is a step in the pipeline rather than "
                "a precondition of starting one. RESTRICT because a historical analysis "
                "names the card it was computed for; the catalog loaders only ever "
                "upsert, so nothing legitimate is blocked."
            ),
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'created'"),
            nullable=False,
            comment=(
                "One of spec §65's nine states. Which transitions are legal belongs to "
                "the state machine, not to this table; the CHECK only refuses a state "
                "that does not exist. 'queued' is not one of them — §65 has the run "
                "endpoint answer with it, and no row ever holds it."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="When the analysis reached a terminal state. NULL until it does.",
        ),
        sa.Column(
            "model_bundle_version",
            PRINTED,
            nullable=True,
            comment=(
                "The model bundle that produced the result — "
                "'pokemon-condition-v0.3.0'. An explicit identifier, never '/latest/' "
                "(spec §31). NULL in V1 because no model exists yet: a documented "
                "absence rather than a gap."
            ),
        ),
        sa.Column(
            "market_snapshot_id",
            sa.Uuid(),
            nullable=True,
            comment=(
                "Which pre-ingested market snapshot the economics were computed "
                "against. No foreign key yet — the table it will point at arrives with "
                "the market-data milestone, and adding the constraint then is cheaper "
                "than changing this column's type."
            ),
        ),
        sa.Column(
            "economic_configuration_id",
            sa.Uuid(),
            nullable=True,
            comment="The fee and cost configuration used. No foreign key yet, as above.",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analyses"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["analysis_sessions.id"],
            name="fk_analyses_session_id_analysis_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name="fk_analyses_card_id_cards",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"status IN ({ANALYSIS_STATES})",
            name="status_is_a_known_analysis_state",
        ),
        sa.CheckConstraint(
            f"completed_at IS NULL OR status IN ({TERMINAL_STATES})",
            name="completed_at_accompanies_a_terminal_status",
        ),
        comment=(
            "One analysis of one card — spec §12. Its reproducibility record is only "
            "partly here: §57's card database and grading rules versions are their own "
            "issue."
        ),
    )
    # PostgreSQL does not index a foreign key's child column for you, and an
    # unindexed one turns the retention sweep's bulk session delete into a
    # sequential scan per row. It also serves "the analyses in this session".
    op.create_index("ix_analyses_session_id", "analyses", ["session_id"])

    op.create_table(
        "images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "analysis_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "CASCADE, so that expiring a session reaches the photographs. Deleting "
                "the row is not deleting the object: the retention job removes the "
                "stored file and verifies it against storage."
            ),
        ),
        sa.Column(
            "side",
            sa.Text(),
            nullable=False,
            comment=(
                "Which view of the card this is. All six of spec §11's values are "
                "accepted from the first migration; V1 writes only front and back, and "
                "the other four are §52's guided photography, which the V1 pipeline "
                "must already be compatible with."
            ),
        ),
        sa.Column(
            "original_uri",
            sa.Text(),
            nullable=False,
            comment=(
                "A server-generated storage key (spec §55) — never a client-supplied "
                "filename or path. Not COLLATE C: it is neither ordered nor "
                "prefix-matched, only fetched by the row that holds it."
            ),
        ),
        sa.Column(
            "normalized_uri",
            sa.Text(),
            nullable=True,
            comment=(
                "The perspective-corrected, cropped, normalized artifact every later ML "
                "stage reads. NULL until the normalization step has run."
            ),
        ),
        sa.Column(
            "width",
            sa.Integer(),
            nullable=True,
            comment=(
                "The normalized artifact's width in pixels, written by the "
                "normalization step alongside normalized_uri — not the original "
                "photograph's, which the upload only bounds rather than records. NULL "
                "until that step has run. If a later stage needs the original's "
                "dimensions, that is a column it adds rather than a meaning to overload "
                "here."
            ),
        ),
        sa.Column(
            "height",
            sa.Integer(),
            nullable=True,
            comment="The normalized artifact's height in pixels, as above.",
        ),
        sa.Column(
            "mime_type",
            sa.Text(),
            nullable=False,
            comment=(
                "The type the file was validated as, never the one the client claimed "
                "(spec §55). No CHECK: which types are accepted is the upload "
                "endpoint's policy, and will change without a migration."
            ),
        ),
        sa.Column(
            "sha256",
            PRINTED,
            nullable=False,
            comment=(
                "A digest over the original bytes, as 64 lowercase hex characters — "
                "bare, not the 'sha256:'-prefixed form the catalog snapshot writes, "
                "because the column already names the algorithm. The preprocessing "
                "cache is keyed on it, which is why it is indexed."
            ),
        ),
        sa.Column(
            "quality_score",
            sa.Double(),
            nullable=True,
            comment=(
                "The quality gate's numeric verdict. NULL until the gate has run. "
                "Deliberately unconstrained in range: spec §19 fixes the four statuses "
                "and says nothing about a scale, so the gate defines it and may add a "
                "CHECK then. An omission on purpose, not an oversight."
            ),
        ),
        sa.Column(
            "quality_status",
            sa.Text(),
            nullable=True,
            comment=(
                "Spec §19's verdict: good, acceptable, poor or unusable. NULL until the "
                "gate has run. 'unusable' stops the analysis and 'poor' continues but "
                "the user must be told — rules that live with the gate, not here."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_images"),
        # One front, one back — and later one angled front, and so on. A retake is
        # a replacement, so nothing downstream has to choose between two fronts,
        # and the retention job has no rejected upload it never knew about. The
        # index this brings also serves "the images of this analysis", so there is
        # no separate index on analysis_id.
        sa.UniqueConstraint("analysis_id", "side", name="uq_images_analysis_id_side"),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["analyses.id"],
            name="fk_images_analysis_id_analyses",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"side IN ({SIDES})", name="side_is_a_known_side"),
        sa.CheckConstraint(
            f"quality_status IS NULL OR quality_status IN ({QUALITY_STATUSES})",
            name="quality_status_is_a_known_status",
        ),
        sa.CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="dimensions_are_positive",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_is_lowercase_hex"),
        comment=(
            "One uploaded photograph and what has been derived from it — spec §11. The "
            "row exists because a validated image is stored, so only the columns a "
            "later pipeline stage fills are nullable."
        ),
    )
    # The content-hash cache's lookup. Not unique: the same photograph uploaded to
    # two analyses is two images, and deduplicating across users is explicitly not
    # a product feature.
    op.create_index("ix_images_sha256", "images", ["sha256"])


def downgrade() -> None:
    # Indexes named explicitly even though `DROP TABLE` would take them, so the
    # reversal reads as the exact inverse of the upgrade. Children first, and
    # nothing else to drop: this revision creates no type, no function and no
    # trigger, which is the point of expressing the vocabularies as CHECKs.
    op.drop_index("ix_images_sha256", table_name="images")
    op.drop_table("images")

    op.drop_index("ix_analyses_session_id", table_name="analyses")
    op.drop_table("analyses")

    op.drop_index("ix_analysis_sessions_expires_at", table_name="analysis_sessions")
    op.drop_table("analysis_sessions")
