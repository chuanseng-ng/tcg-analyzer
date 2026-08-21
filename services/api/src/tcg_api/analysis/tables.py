"""The analysis spine's schema, as SQLAlchemy Core.

Three tables: spec §12's `analysis_sessions` and `analyses`, and §11's `images`.
Together they tie an anonymous session to its photographs, to the card the user
confirmed and eventually to a result. Nothing writes to them yet — the upload
endpoint, the state machine and the confirmation are separate issues — but the
shape has to be right first, because two of the properties below cannot be
retrofitted against a table already holding user data.

The tables attach to the service-wide `MetaData` in `tcg_api.tables`, which
`database/migrations/env.py` compares a database against. **Core, not ORM**, on
the same terms as the catalog: the vocabularies are frozen `StrEnum`s in
`tcg_domain.analysis` and an adapter constructs from rows rather than mapping to
them.

Six things about this schema are load-bearing:

* **`side` already admits all six values.** V1 writes `front` and `back`; spec
  §52's guided photography adds angled and surface-lit captures, and §52 says in
  as many words that "the V1 image pipeline must be compatible with this future
  input". Admitting them now costs a longer CHECK constraint. Adding them later
  costs a migration against a table of user photographs.
* **`expires_at` is not decorative.** Spec §54 makes expiry the default and
  retention the exception, and gives that rule teeth only if the column exists
  and is `NOT NULL` from the first migration. It has no server default: the
  retention period is a policy, and a policy hidden in a column default is one
  nobody reviews.
* **`analyses.card_id` is nullable, deliberately.** The card is unknown until the
  user confirms it (spec §20), so `NOT NULL` here would make the confirmed card a
  precondition of starting rather than a step in the pipeline.
* **The vocabularies are enums, and the columns are `TEXT` with a `CHECK`.**
  There is no `CREATE TYPE` anywhere in this schema history:
  `database/migrations/README.md` keeps the deployment platform open, a
  `downgrade()` that drops a type has to name it separately, and a value added to
  a PostgreSQL enum can never be removed. A named CHECK reverses exactly and
  names itself in the `IntegrityError` a caller sees.
* **One image per side, per analysis.** A retake replaces the front rather than
  appending a second one, so nothing downstream has to decide which front is the
  real one, and the retention job has no orphaned reject to miss.
* **An image row means a stored, validated image.** Everything the upload knows
  by then — the storage key, the sniffed MIME type, the digest — is `NOT NULL`.
  Only what a later stage computes is nullable: `normalized_uri`, `width`,
  `height`, `quality_score` and `quality_status`. `width` and `height` are the
  *normalized* artifact's, which is what the normalization issue's own scope
  says it writes.

Spec §57's reproducibility record is **not** pre-empted here. §12's own column
list is transcribed and stops; `card_database_version` and
`grading_rules_version` are their own issue, and must hold a published
identifier resolved when the analysis ran rather than a pointer to "current".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_domain.analysis import (
    TERMINAL_STATUSES,
    AnalysisStatus,
    ImageSide,
    QualityStatus,
    SessionStatus,
)

# `analyses.card_id` points into the catalog, so the catalog is a hard dependency
# of this module rather than merely of the migration environment: a `sa.ForeignKey`
# resolves against the `MetaData` it is attached to, and without `cards` on it
# `CreateTable(analyses)` raises NoReferencedTableError. The column object is
# referenced directly rather than by the string "cards.id" so that the dependency
# is visible to a reader and to a type checker, and cannot be a silent typo.
from tcg_api.catalog.tables import cards
from tcg_api.tables import PRINTED, metadata

__all__ = ["TABLES", "analyses", "analysis_sessions", "images"]


def _one_of(column: str, values: Iterable[str]) -> str:
    """Render ``column IN ('a', 'b')`` from a domain vocabulary.

    Built from the enum rather than written out, so a member added to
    `tcg_domain.analysis` cannot be one the database silently refuses. The
    migration writes the same list as a literal — a migration is a snapshot of
    what was applied and must not change when this module does — and
    `test_analysis_tables.py` checks that the two still agree.
    """
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


#: Spec §65's terminal states, in the enum's own order so the rendered DDL is
#: stable. `completed_at` is only meaningful for one of these.
_TERMINAL: Final = tuple(status for status in AnalysisStatus if status in TERMINAL_STATUSES)


analysis_sessions = sa.Table(
    "analysis_sessions",
    metadata,
    sa.Column(
        "id",
        sa.Uuid(),
        primary_key=True,
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
        nullable=False,
        server_default=sa.func.now(),
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
        nullable=False,
        server_default=sa.text(f"'{SessionStatus.ACTIVE.value}'"),
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
    sa.UniqueConstraint(
        "anonymous_session_id",
        name="uq_analysis_sessions_anonymous_session_id",
    ),
    # Short names: the naming convention supplies the `ck_analysis_sessions_`
    # prefix, in this file and in the migration alike.
    sa.CheckConstraint(
        _one_of("status", SessionStatus),
        name="status_is_a_known_session_state",
    ),
    # A session that expired before it existed is a clock or a caller bug, and the
    # retention sweep would read it as already due.
    sa.CheckConstraint("expires_at > created_at", name="expires_after_it_was_created"),
    # Serves the retention sweep — the rows now due, oldest first. The unique
    # constraint above serves the only other query there is: one session, by the
    # token the client presented.
    sa.Index("ix_analysis_sessions_expires_at", "expires_at"),
    comment=(
        "One anonymous session — spec §12, §53. V1 has no accounts, so this is the "
        "whole of a user's continuity, and it expires."
    ),
)


analyses = sa.Table(
    "analyses",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "session_id",
        sa.Uuid(),
        sa.ForeignKey(
            "analysis_sessions.id",
            ondelete="CASCADE",
            name="fk_analyses_session_id_analysis_sessions",
        ),
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
        sa.ForeignKey(cards.c.id, ondelete="RESTRICT", name="fk_analyses_card_id_cards"),
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
        nullable=False,
        server_default=sa.text(f"'{AnalysisStatus.CREATED.value}'"),
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
        nullable=False,
        server_default=sa.func.now(),
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
            "The model bundle that produced the result — 'pokemon-condition-v0.3.0'. "
            "An explicit identifier, never '/latest/' (spec §31). NULL in V1 because "
            "no model exists yet: a documented absence rather than a gap."
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
    sa.CheckConstraint(
        _one_of("status", AnalysisStatus),
        name="status_is_a_known_analysis_state",
    ),
    # A completion time on an analysis that has not finished is not a smaller
    # version of the truth; it is a different claim.
    sa.CheckConstraint(
        f"completed_at IS NULL OR {_one_of('status', _TERMINAL)}",
        name="completed_at_accompanies_a_terminal_status",
    ),
    sa.Index("ix_analyses_session_id", "session_id"),
    # No index on `card_id`: nothing deletes from the catalog, so the RESTRICT
    # check never runs in anger, and no query yet asks which analyses named a
    # card. No index on `status` either — the job runner's pick-up query is its
    # own issue, and should add the partial index that query actually wants.
    comment=(
        "One analysis of one card — spec §12. Its reproducibility record is only "
        "partly here: §57's card database and grading rules versions are their own "
        "issue."
    ),
)


images = sa.Table(
    "images",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "analysis_id",
        sa.Uuid(),
        sa.ForeignKey("analyses.id", ondelete="CASCADE", name="fk_images_analysis_id_analyses"),
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
            "photograph's, which the upload only bounds rather than records. "
            "NULL until that step has run. If a later stage needs the original's "
            "dimensions, that is a column it adds rather than a meaning to "
            "overload here."
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
            "The quality gate's numeric verdict, in [0, 1] with 1 being best. "
            "NULL until the gate has run. Spec §19 fixes the four statuses and says "
            "nothing about a scale, so the gate defined this one (#36): the smallest "
            "headroom any condition had above its poor threshold, so the weakest "
            "link governs exactly as the status is the worst finding."
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
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    # One front, one back — and later one angled front, and so on. A retake is a
    # replacement, so nothing downstream has to choose between two fronts, and the
    # retention job has no rejected upload it never knew about.
    sa.UniqueConstraint("analysis_id", "side", name="uq_images_analysis_id_side"),
    sa.CheckConstraint(_one_of("side", ImageSide), name="side_is_a_known_side"),
    sa.CheckConstraint(
        f"quality_status IS NULL OR {_one_of('quality_status', QualityStatus)}",
        name="quality_status_is_a_known_status",
    ),
    # Zero is not a smaller image, it is a division by zero waiting for the
    # centering ratios. Written as two NULL-tolerant halves because both columns
    # stay empty until normalization has run.
    sa.CheckConstraint(
        "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
        name="dimensions_are_positive",
    ),
    # The range #31 left for the gate to define (#36). A score outside [0, 1] is
    # not a worse photograph, it is a gate that has miscomputed — and every
    # consumer of it, the score bar included, reads it as a fraction.
    sa.CheckConstraint(
        "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
        name="quality_score_is_a_unit_interval",
    ),
    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_is_lowercase_hex"),
    # The cache lookup: every image already processed from these bytes. Not
    # unique — the same photograph uploaded to two analyses is two images, and
    # deduplicating across users is explicitly not a product feature.
    sa.Index("ix_images_sha256", "sha256"),
    # No separate index on `analysis_id`: uq_images_analysis_id_side leads with
    # it, so "the images of this analysis" is already served.
    comment=(
        "One uploaded photograph and what has been derived from it — spec §11. The "
        "row exists because a validated image is stored, so only the columns a "
        "later pipeline stage fills are nullable."
    ),
)


#: Every table this module contributes to the shared `MetaData`. Declared so that
#: the schema can still be asserted as a closed set now that the `MetaData` is
#: shared between two domains.
TABLES: Final = (analysis_sessions, analyses, images)
