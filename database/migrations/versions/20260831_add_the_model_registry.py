"""add the model registry

Spec §58's model registry and §59's versioned bundles: the store that lets an
analysis record `condition-model-v0.3.0` and mean one immutable artifact
forever. The seventh schema domain, and one table — the registry stores the
reference, object storage holds the bytes.

The shape and the reasoning live in
`services/api/src/tcg_api/models/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Five things worth knowing before reading the DDL:

* **Only `status` may change, and only forward.** The record trigger refuses
  any UPDATE that moves a write-once column — the `analyses` reproducibility
  trigger's WHEN-clause precedent — and the transition trigger permits a
  status move only to a strictly later state in the lifecycle
  `experimental, candidate, production, retired`. `retired` is therefore
  terminal by construction, and `production` is never demoted in place.
* **DELETE is refused, unlike every other domain here.** A registry row is the
  record that a version existed and must outlive the artifact's retirement;
  its disposal is `retired`, not removal.
* **One `production` bundle per model name**, held by a partial unique index —
  spec §59's "never overwrite production model files" in schema form, rather
  than an operator remembering to retire the predecessor first.
* **`training_dataset_version` references `dataset_versions.version`**, the
  identifier rather than the surrogate key, so the row is legible without a
  join. RESTRICT for #153's reason: a corpus a registered model trained on
  cannot be un-published.
* **No rows are inserted.** Heuristic analyzer versions are code constants,
  never registry rows; the first row is written when the first trained
  artifact exists (late M7 or M8).

Two trigger functions rather than one, because the two refusals give different
instructions: the record refusal tells the operator to register a new version,
the transition refusal names the illegal move.

Alembic compares no triggers, so `compare_metadata` will not notice if this and
`tables.py` drift apart. `services/api/tests/test_models_schema.py` asserts the
refusals actually happen; that test is the only guard there is.

Revision ID: e5b8a3d47f21
Revises: a1c9e47d20b6
Create Date: 2026-08-31 00:00:00.000000+00:00
Refs: M7, spec §58-59, #189

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5b8a3d47f21"
down_revision: str | None = "a1c9e47d20b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text, ordered by byte rather than by whatever locale the server was
# initialised with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

# Written out as literals rather than built from the domain the table module
# reads them from. A migration is a snapshot of what was applied;
# `test_models_tables.py` checks that the two still agree.
NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
VERSION_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*-v\d+\.\d+\.\d+$"

# The write-once columns — everything but `status` — rendered into the record
# trigger's `WHEN` clause exactly as `tcg_api.models.tables` renders them.
RECORD_COLUMNS = (
    "id",
    "model_name",
    "model_version",
    "training_dataset_version",
    "training_config",
    "metrics",
    "artifact_location",
    "created_at",
)

RECORD_CHANGED = "\n      OR ".join(
    f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in RECORD_COLUMNS
)

# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: the same statement is declared through `sa.DDL` in `tables.py`, and
# `sa.DDL` runs its statement through Python's `%` interpolation, so a format
# specifier in the body fails at compile time. Kept identical here so the two
# copies can be diffed by eye.
#
# `restrict_violation` is SQLSTATE class 23, which SQLAlchemy surfaces as an
# `IntegrityError` — the same shape as every other constraint in this schema,
# rather than an `InternalError` a caller would have to special-case.
IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION model_bundles_are_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE USING
        ERRCODE = 'restrict_violation',
        MESSAGE = 'model bundle ' || OLD.model_version || ' is immutable: '
                  || TG_OP || ' was refused',
        HINT    = 'Register a new bundle version rather than rewriting one.';
END;
$$;
"""

# `array_position` answers NULL for a value outside the array, `NULL <= x` is
# NULL, and an `IF NULL` does not raise — which is correct: an unknown status
# is the membership CHECK's refusal, not this trigger's.
STATUS_FUNCTION = """
CREATE OR REPLACE FUNCTION model_bundle_status_walks_forward()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    lifecycle text[] := ARRAY['experimental', 'candidate', 'production', 'retired'];
BEGIN
    IF array_position(lifecycle, NEW.status)
       <= array_position(lifecycle, OLD.status) THEN
        RAISE USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'model bundle ' || OLD.model_version || ' cannot move from '
                      || OLD.status || ' to ' || NEW.status,
            HINT    = 'The lifecycle walks forward only: experimental, candidate, production, retired.';
    END IF;
    RETURN NEW;
END;
$$;
"""

IMMUTABLE_TRIGGER = f"""
CREATE TRIGGER trg_model_bundles_immutable
BEFORE UPDATE ON model_bundles
FOR EACH ROW
WHEN ({RECORD_CHANGED})
EXECUTE FUNCTION model_bundles_are_immutable();
"""

UNDELETABLE_TRIGGER = """
CREATE TRIGGER trg_model_bundles_undeletable
BEFORE DELETE ON model_bundles
FOR EACH ROW EXECUTE FUNCTION model_bundles_are_immutable();
"""

STATUS_TRIGGER = """
CREATE TRIGGER trg_model_bundles_status_walks_forward
BEFORE UPDATE ON model_bundles
FOR EACH ROW
WHEN (OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION model_bundle_status_walks_forward();
"""


def upgrade() -> None:
    op.create_table(
        "model_bundles",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="A surrogate key. The record's identity is `model_version`.",
        ),
        sa.Column(
            "model_name",
            PRINTED,
            nullable=False,
            comment="The model family — 'condition-model', 'grading-psa'. Spec §58's model_name: the name the four-state lifecycle is scoped to, and the name uq_model_bundles_one_production_per_name holds to one production bundle at a time.",
        ),
        sa.Column(
            "model_version",
            PRINTED,
            nullable=False,
            comment="The full immutable identifier — 'grading-psa-v0.2.0', spec §58's model_version and what `analyses.model_bundle_version` records. The grammar CHECK is `dataset_versions.version`'s, so '/latest/' is unstorable here exactly as it is there (§59).",
        ),
        sa.Column(
            "training_dataset_version",
            PRINTED,
            nullable=False,
            comment="The frozen corpus this bundle trained on — spec §58's training_dataset_version, referencing `dataset_versions.version` by identifier rather than by surrogate key so the row is legible without a join. RESTRICT for #153's reason: a version a registered model trained on cannot be un-published.",
        ),
        sa.Column(
            "training_config",
            postgresql.JSONB(),
            nullable=False,
            comment="The configuration the training run is reproducible through — spec §60. Stored whole as JSONB: hyperparameters have no fixed shape across model families, and the registry records them rather than interpreting them.",
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(),
            nullable=False,
            comment="The evaluation metrics recorded at registration, in the per-split shape #188's benchmark writes. A learned model enters only through that benchmark, so a row without metrics would be a bundle nobody measured.",
        ),
        sa.Column(
            "artifact_location",
            sa.Text(),
            nullable=False,
            comment="The object-storage key the bundle's bytes live under, read through the ObjectStorage port. A reference, never the bytes: model weights stay out of git and out of the database, and the CHECKs make a '/latest/' pointer and a blank key unstorable (§59).",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            comment="Spec §58's lifecycle: experimental, candidate, production or retired. The one mutable column — trg_model_bundles_status_walks_forward permits a move to a strictly later state only, so `retired` is terminal and production is never demoted in place. No server default: the registration path writes 'experimental' explicitly, because a default is a state nobody chose.",
        ),
        sa.CheckConstraint(f"model_name ~ '{NAME_PATTERN}'", name="name_is_a_lowercase_slug"),
        sa.CheckConstraint(
            f"model_version ~ '{VERSION_PATTERN}'", name="version_is_an_explicit_identifier"
        ),
        sa.CheckConstraint(
            r"model_version ~ ('^' || model_name || '-v[0-9]+\.[0-9]+\.[0-9]+$')",
            name="version_names_its_model",
        ),
        sa.CheckConstraint(
            "position('latest' in artifact_location) = 0",
            name="location_never_says_latest",
        ),
        sa.CheckConstraint("btrim(artifact_location) <> ''", name="location_is_not_blank"),
        sa.CheckConstraint(
            "status IN ('experimental', 'candidate', 'production', 'retired')",
            name="status_is_a_known_status",
        ),
        sa.ForeignKeyConstraint(
            ["training_dataset_version"],
            ["dataset_versions.version"],
            name="fk_model_bundles_training_dataset_version_dataset_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_bundles"),
        sa.UniqueConstraint("model_version", name="uq_model_bundles_model_version"),
        comment="One registered model bundle — spec §58's model registry. Write-once apart from `status`: trg_model_bundles_immutable refuses any other UPDATE and trg_model_bundles_undeletable refuses a DELETE, because an analysis that recorded `model_bundle_version` must keep meaning what it meant. Empty until the first trained artifact: heuristic analyzer versions are code constants, never rows here.",
    )
    op.create_index(
        "uq_model_bundles_one_production_per_name",
        "model_bundles",
        ["model_name"],
        unique=True,
        postgresql_where=sa.text("status = 'production'"),
    )

    op.execute(IMMUTABLE_FUNCTION)
    op.execute(STATUS_FUNCTION)
    op.execute(IMMUTABLE_TRIGGER)
    op.execute(UNDELETABLE_TRIGGER)
    op.execute(STATUS_TRIGGER)


def downgrade() -> None:
    # `DROP TABLE` would take each trigger with it, but not the functions, so
    # those are named explicitly and dropped last.
    op.execute("DROP TRIGGER IF EXISTS trg_model_bundles_status_walks_forward ON model_bundles")
    op.execute("DROP TRIGGER IF EXISTS trg_model_bundles_undeletable ON model_bundles")
    op.execute("DROP TRIGGER IF EXISTS trg_model_bundles_immutable ON model_bundles")
    op.drop_index(
        "uq_model_bundles_one_production_per_name",
        table_name="model_bundles",
        postgresql_where=sa.text("status = 'production'"),
    )
    op.drop_table("model_bundles")
    op.execute("DROP FUNCTION IF EXISTS model_bundles_are_immutable()")
    op.execute("DROP FUNCTION IF EXISTS model_bundle_status_walks_forward()")
