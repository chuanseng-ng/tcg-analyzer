"""Spec §58's model registry, as one SQLAlchemy Core table.

The table attaches to the service-wide `MetaData` in `tcg_api.tables` and the
domain is registered in `tcg_api.table_registry` — a domain the registry does
not import is a domain `alembic revision --autogenerate` proposes dropping.

Five things about this schema are load-bearing:

* **Only `status` may change, and only forward.** Everything else on the row is
  what an analysis's `model_bundle_version` will mean forever, so the record
  trigger refuses any other UPDATE — the `analyses` reproducibility trigger's
  WHEN-clause precedent, inverted from "write once" to "written at INSERT" —
  and a second trigger permits a status move only to a strictly later state in
  :data:`LIFECYCLE`. `retired` is therefore terminal by construction, and
  `production` is never demoted in place.
* **DELETE is refused, unlike every other domain here.** The datasets domain
  leaves DELETE open for §54's disposal and a contributor's withdrawal; a
  registry row is the record that a version existed, must outlive the
  artifact's retirement, and has `retired` as its disposal.
* **One `production` bundle per model name.** Spec §59's "never overwrite
  production model files", held by a partial unique index rather than by an
  operator remembering to retire the predecessor first.
* **`training_dataset_version` references the identifier, not the surrogate.**
  `dataset_members` takes the UUID because a membership row is meaningless
  outside a join anyway; a registry row is read by people and by manifests, and
  identifiers are how everything downstream of the database names a dataset
  version. `uq_dataset_versions_version` makes the reference legal, and
  RESTRICT holds either way: a corpus a registered model trained on cannot be
  un-published.
* **The table lands empty.** Heuristic analyzer versions are code constants
  composed into `PIPELINE_VERSION` and `CONDITION_VERSION`, never rows here —
  a row names a *trained* artifact with a dataset version and metrics, and
  null-filling those for code would make the registry lie.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_domain import VERSION_PATTERN

# Referenced as a column object rather than by the string
# "dataset_versions.version" for the reason `market/tables.py` gives: the
# dependency is then visible to a reader and to mypy, and cannot be a silent
# typo. The direction is safe — nothing in the datasets domain reads this one.
from tcg_api.datasets.tables import dataset_versions
from tcg_api.tables import PRINTED, metadata, one_of

__all__ = [
    "LIFECYCLE",
    "RECORD_COLUMNS",
    "TABLES",
    "ModelStatus",
    "model_bundles",
]


class ModelStatus(StrEnum):
    """Spec §58's four-state lifecycle, in the order the walk runs."""

    EXPERIMENTAL = "experimental"
    CANDIDATE = "candidate"
    PRODUCTION = "production"
    RETIRED = "retired"


#: The lifecycle in walking order. The order is load-bearing: the transition
#: trigger compares positions in this tuple, rendered as a SQL array, and a
#: member reordered here would legalise a different set of moves.
LIFECYCLE: Final = tuple(status.value for status in ModelStatus)

#: The model-family grammar — the version grammar's stem, without the `-vX.Y.Z`
#: suffix the CHECK below requires `model_version` to add.
_NAME_PATTERN: Final = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

#: The identifier grammar every immutable version in this schema shares. Taken
#: from `tcg_domain`'s own pattern rather than retyped, so the database refuses
#: exactly what `CardDatabaseVersion` refuses — a '/latest/' pointer included.
_VERSION_PATTERN: Final = VERSION_PATTERN.pattern


model_bundles = sa.Table(
    "model_bundles",
    metadata,
    sa.Column(
        "id",
        sa.Uuid(),
        primary_key=True,
        comment="A surrogate key. The record's identity is `model_version`.",
    ),
    sa.Column(
        "model_name",
        PRINTED,
        nullable=False,
        comment=(
            "The model family — 'condition-model', 'grading-psa'. Spec §58's model_name: "
            "the name the four-state lifecycle is scoped to, and the name "
            "uq_model_bundles_one_production_per_name holds to one production bundle at "
            "a time."
        ),
    ),
    sa.Column(
        "model_version",
        PRINTED,
        nullable=False,
        comment=(
            "The full immutable identifier — 'grading-psa-v0.2.0', spec §58's "
            "model_version and what `analyses.model_bundle_version` records. The grammar "
            "CHECK is `dataset_versions.version`'s, so '/latest/' is unstorable here "
            "exactly as it is there (§59)."
        ),
    ),
    sa.Column(
        "training_dataset_version",
        PRINTED,
        sa.ForeignKey(
            dataset_versions.c.version,
            ondelete="RESTRICT",
            name="fk_model_bundles_training_dataset_version_dataset_versions",
        ),
        nullable=False,
        comment=(
            "The frozen corpus this bundle trained on — spec §58's "
            "training_dataset_version, referencing `dataset_versions.version` by "
            "identifier rather than by surrogate key so the row is legible without a "
            "join. RESTRICT for #153's reason: a version a registered model trained on "
            "cannot be un-published."
        ),
    ),
    sa.Column(
        "training_config",
        postgresql.JSONB(),
        nullable=False,
        comment=(
            "The configuration the training run is reproducible through — spec §60. "
            "Stored whole as JSONB: hyperparameters have no fixed shape across model "
            "families, and the registry records them rather than interpreting them."
        ),
    ),
    sa.Column(
        "metrics",
        postgresql.JSONB(),
        nullable=False,
        comment=(
            "The evaluation metrics recorded at registration, in the per-split shape "
            "#188's benchmark writes. A learned model enters only through that "
            "benchmark, so a row without metrics would be a bundle nobody measured."
        ),
    ),
    sa.Column(
        "artifact_location",
        sa.Text(),
        nullable=False,
        comment=(
            "The object-storage key the bundle's bytes live under, read through the "
            "ObjectStorage port. A reference, never the bytes: model weights stay out "
            "of git and out of the database, and the CHECKs make a '/latest/' pointer "
            "and a blank key unstorable (§59)."
        ),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "status",
        sa.Text(),
        nullable=False,
        comment=(
            "Spec §58's lifecycle: experimental, candidate, production or retired. The "
            "one mutable column — trg_model_bundles_status_walks_forward permits a move "
            "to a strictly later state only, so `retired` is terminal and production is "
            "never demoted in place. No server default: the registration path writes "
            "'experimental' explicitly, because a default is a state nobody chose."
        ),
    ),
    sa.UniqueConstraint("model_version", name="uq_model_bundles_model_version"),
    sa.CheckConstraint(f"model_name ~ '{_NAME_PATTERN}'", name="name_is_a_lowercase_slug"),
    sa.CheckConstraint(
        f"model_version ~ '{_VERSION_PATTERN}'", name="version_is_an_explicit_identifier"
    ),
    # Anchored to the whole version, not merely its prefix: a starts-with
    # check would file 'grading-vault-v1.0.0' under 'grading', and the
    # one-production index would then scope the wrong family. `model_name` is
    # regex-safe under its own slug CHECK, and no `%` reaches a driver's
    # paramstyle.
    sa.CheckConstraint(
        r"model_version ~ ('^' || model_name || '-v[0-9]+\.[0-9]+\.[0-9]+$')",
        name="version_names_its_model",
    ),
    sa.CheckConstraint(
        "position('latest' in artifact_location) = 0",
        name="location_never_says_latest",
    ),
    sa.CheckConstraint("btrim(artifact_location) <> ''", name="location_is_not_blank"),
    # A closed lifecycle gets a membership CHECK like `DatasetSplit`'s. This is
    # deliberately not `grading_rules.company`'s open allow-list: a fifth
    # lifecycle state is a change to what the walk *means*, not a new member.
    sa.CheckConstraint(one_of("status", ModelStatus), name="status_is_a_known_status"),
    # Spec §59's "never overwrite production model files" in schema form: the
    # first partial *unique* index in this schema, scoped to the one state the
    # rule is about.
    sa.Index(
        "uq_model_bundles_one_production_per_name",
        "model_name",
        unique=True,
        postgresql_where=sa.text("status = 'production'"),
    ),
    comment=(
        "One registered model bundle — spec §58's model registry. Write-once apart from "
        "`status`: trg_model_bundles_immutable refuses any other UPDATE and "
        "trg_model_bundles_undeletable refuses a DELETE, because an analysis that "
        "recorded `model_bundle_version` must keep meaning what it meant. Empty until "
        "the first trained artifact: heuristic analyzer versions are code constants, "
        "never rows here."
    ),
)

TABLES: Final = (model_bundles,)


# ---------------------------------------------------------------------------
# Only `status` may change, and only forward
# ---------------------------------------------------------------------------
# Two functions rather than one, and the reason is the message: the record
# refusal tells the operator to register a new version, the transition refusal
# names the illegal move. Three standing caveats, as in every other domain:
# `plpgsql` needs no `CREATE EXTENSION`; TRUNCATE bypasses row-level triggers,
# which is the only reason the integration fixtures can empty this table; and
# Alembic compares no triggers at all, so `test_models_schema.py`'s refusal
# tests are the only guard against this and the migration drifting apart.
def _ddl(statement: str) -> sa.DDL:
    """`sa.DDL` is unannotated in SQLAlchemy's own types, and mypy runs strict here."""
    return sa.DDL(statement)  # type: ignore[no-untyped-call]


#: The write-once columns — everything but `status`. Rendered into the record
#: trigger's `WHEN` clause, so a column added to the table is guarded by adding
#: it here, and `test_models_tables.py` fails if one is not.
RECORD_COLUMNS: Final = (
    "id",
    "model_name",
    "model_version",
    "training_dataset_version",
    "training_config",
    "metrics",
    "artifact_location",
    "created_at",
)

_CHANGED: Final = "\n      OR ".join(
    f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in RECORD_COLUMNS
)

_LIFECYCLE_ARRAY: Final = "ARRAY[" + ", ".join(f"'{status}'" for status in LIFECYCLE) + "]"

# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: `sa.DDL` runs its statement through Python's `%` interpolation, so a
# format specifier in the body fails at compile time. Do not "simplify" it back.
_IMMUTABLE_FUNCTION: Final = _ddl(
    """
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
)

# `array_position` answers NULL for a value outside the array, `NULL <= x` is
# NULL, and an `IF NULL` does not raise — which is correct: an unknown status
# is the membership CHECK's refusal, not this trigger's.
_STATUS_FUNCTION: Final = _ddl(
    f"""
    CREATE OR REPLACE FUNCTION model_bundle_status_walks_forward()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
        lifecycle text[] := {_LIFECYCLE_ARRAY};
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
)

_IMMUTABLE_TRIGGER: Final = _ddl(
    f"""
    CREATE TRIGGER trg_model_bundles_immutable
    BEFORE UPDATE ON model_bundles
    FOR EACH ROW
    WHEN ({_CHANGED})
    EXECUTE FUNCTION model_bundles_are_immutable();
    """
)

_UNDELETABLE_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_model_bundles_undeletable
    BEFORE DELETE ON model_bundles
    FOR EACH ROW EXECUTE FUNCTION model_bundles_are_immutable();
    """
)

_STATUS_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_model_bundles_status_walks_forward
    BEFORE UPDATE ON model_bundles
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION model_bundle_status_walks_forward();
    """
)

# Two statements, two DDL objects: the asyncpg driver prepares each statement it
# is handed, and a prepared statement may not contain more than one.
_DROP_IMMUTABLE_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_model_bundles_immutable ON model_bundles"
)

_DROP_UNDELETABLE_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_model_bundles_undeletable ON model_bundles"
)

_DROP_STATUS_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_model_bundles_status_walks_forward ON model_bundles"
)

_DROP_IMMUTABLE_FUNCTION: Final = _ddl("DROP FUNCTION IF EXISTS model_bundles_are_immutable()")

_DROP_STATUS_FUNCTION: Final = _ddl("DROP FUNCTION IF EXISTS model_bundle_status_walks_forward()")

sa.event.listen(model_bundles, "after_create", _IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"))
sa.event.listen(model_bundles, "after_create", _STATUS_FUNCTION.execute_if(dialect="postgresql"))
sa.event.listen(model_bundles, "after_create", _IMMUTABLE_TRIGGER.execute_if(dialect="postgresql"))
sa.event.listen(
    model_bundles, "after_create", _UNDELETABLE_TRIGGER.execute_if(dialect="postgresql")
)
sa.event.listen(model_bundles, "after_create", _STATUS_TRIGGER.execute_if(dialect="postgresql"))
sa.event.listen(model_bundles, "before_drop", _DROP_STATUS_TRIGGER.execute_if(dialect="postgresql"))
sa.event.listen(
    model_bundles, "before_drop", _DROP_UNDELETABLE_TRIGGER.execute_if(dialect="postgresql")
)
sa.event.listen(
    model_bundles, "before_drop", _DROP_IMMUTABLE_TRIGGER.execute_if(dialect="postgresql")
)
# Dropped last: `DROP TABLE` takes each trigger with it but never a function.
sa.event.listen(
    model_bundles, "before_drop", _DROP_IMMUTABLE_FUNCTION.execute_if(dialect="postgresql")
)
sa.event.listen(
    model_bundles, "before_drop", _DROP_STATUS_FUNCTION.execute_if(dialect="postgresql")
)
