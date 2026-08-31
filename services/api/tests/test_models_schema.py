"""The model registry against a real PostgreSQL — #189.

`test_models_tables.py` asserts what was *declared*; this asserts what the
database actually does after the migration has run. Alembic compares a check's
name but never its text, and no triggers at all — so the refusals here are the
only guard against the declaration and the migration drifting apart.

Most of what is here is the lifecycle, one move per test. That is deliberate
repetition: "only `status` may change, and only forward" is the rule most
likely to be softened later by somebody with a bundle to fix up, and a single
parametrised "bad transition is refused" would let half the moves be lost in
one edit.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.datasets.tables import dataset_versions
from tcg_api.models.tables import model_bundles

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
    ),
]

DATASET_VERSION = "pokemon-condition-v0.1.0"

#: A bundle every constraint accepts. Each test below spoils exactly one thing,
#: so a failure names the rule that was broken rather than "something was wrong".
LEGAL: dict[str, Any] = {
    "model_name": "grading-psa",
    "model_version": "grading-psa-v0.1.0",
    "training_dataset_version": DATASET_VERSION,
    "training_config": {"epochs": 40},
    "metrics": {"validation": {"macro_f1": 0.5}},
    "artifact_location": "model-bundles/grading-psa-v0.1.0/weights.pt",
    "status": "experimental",
}


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` deliberately leaves the database at `base`."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture(autouse=True)
def dataset_version() -> Iterator[None]:
    """TRUNCATE bypasses row-level triggers, which is the only reason
    `model_bundles` empties at all — a DELETE is refused by its own trigger."""
    execute(sa.text("TRUNCATE model_bundles, dataset_versions RESTART IDENTITY CASCADE"))
    execute(
        sa.insert(dataset_versions),
        {"id": uuid.uuid4(), "version": DATASET_VERSION, "split_seed": 1},
    )
    yield
    execute(sa.text("TRUNCATE model_bundles, dataset_versions RESTART IDENTITY CASCADE"))


def execute(statement: Any, values: Any = None) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(statement, values) if values else await connection.execute(
                    statement
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def fetch(statement: Any) -> list[Any]:
    async def scenario() -> list[Any]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return list((await connection.execute(statement)).all())
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def insert_bundle(**overrides: Any) -> uuid.UUID:
    identifier = uuid.uuid4()
    execute(sa.insert(model_bundles), {"id": identifier, **LEGAL, **overrides})
    return identifier


def move(bundle_id: uuid.UUID, status: str) -> None:
    execute(sa.update(model_bundles).where(model_bundles.c.id == bundle_id).values(status=status))


def status_of(bundle_id: uuid.UUID) -> str:
    rows = fetch(sa.select(model_bundles.c.status).where(model_bundles.c.id == bundle_id))
    assert len(rows) == 1
    return str(rows[0].status)


# ---------------------------------------------------------------------------
# Write-once, apart from status
# ---------------------------------------------------------------------------


def test_a_write_once_column_cannot_move() -> None:
    bundle_id = insert_bundle()

    with pytest.raises(IntegrityError, match="is immutable"):
        execute(
            sa.update(model_bundles)
            .where(model_bundles.c.id == bundle_id)
            .values(artifact_location="model-bundles/somewhere-else/weights.pt")
        )


def test_the_metrics_cannot_be_improved_afterwards() -> None:
    """The registration's metrics are the registration's — a better number
    belongs to a new bundle that earned it."""
    bundle_id = insert_bundle()

    with pytest.raises(IntegrityError, match="is immutable"):
        execute(
            sa.update(model_bundles)
            .where(model_bundles.c.id == bundle_id)
            .values(metrics={"validation": {"macro_f1": 0.9}})
        )


def test_rewriting_the_same_values_is_a_no_op() -> None:
    """`IS DISTINCT FROM`, so a retried transaction is safe — the
    reproducibility trigger's precedent."""
    bundle_id = insert_bundle()

    execute(
        sa.update(model_bundles)
        .where(model_bundles.c.id == bundle_id)
        .values(artifact_location=LEGAL["artifact_location"])
    )

    assert status_of(bundle_id) == "experimental"


def test_a_status_move_smuggling_another_column_is_refused() -> None:
    """The record trigger's WHEN clause is true, so the move is refused whole —
    and the *immutable* message is the one pinned here, because PostgreSQL
    fires same-event triggers alphabetically and `trg_model_bundles_immutable`
    sorts before `trg_model_bundles_status_walks_forward`. A rename of either
    trigger that swapped which refusal an operator sees would fail this."""
    bundle_id = insert_bundle()

    with pytest.raises(IntegrityError, match="is immutable"):
        execute(
            sa.update(model_bundles)
            .where(model_bundles.c.id == bundle_id)
            .values(status="candidate", artifact_location="model-bundles/elsewhere/weights.pt")
        )


def test_a_bundle_cannot_be_deleted() -> None:
    """Unlike every other domain here: a registry row is the record that a
    version existed, and its disposal is `retired`, not removal."""
    bundle_id = insert_bundle()

    with pytest.raises(IntegrityError, match="is immutable"):
        execute(sa.delete(model_bundles).where(model_bundles.c.id == bundle_id))


# ---------------------------------------------------------------------------
# The lifecycle walks forward
# ---------------------------------------------------------------------------


def test_the_lifecycle_walks_forward_stepwise() -> None:
    bundle_id = insert_bundle()

    for status in ("candidate", "production", "retired"):
        move(bundle_id, status)

    assert status_of(bundle_id) == "retired"


def test_an_abandoned_experiment_may_retire_directly() -> None:
    """Forward-only with skips allowed: a bundle nobody promotes still ends."""
    bundle_id = insert_bundle()

    move(bundle_id, "retired")

    assert status_of(bundle_id) == "retired"


def test_production_cannot_be_demoted() -> None:
    bundle_id = insert_bundle(status="production")

    with pytest.raises(IntegrityError, match="cannot move from"):
        move(bundle_id, "candidate")


def test_retired_is_terminal() -> None:
    bundle_id = insert_bundle(status="retired")

    with pytest.raises(IntegrityError, match="cannot move from"):
        move(bundle_id, "production")


def test_a_status_rewritten_in_place_is_a_no_op() -> None:
    """`experimental` to `experimental` is not a move — the WHEN clause makes
    it a no-op rather than a refusal, so a retried transaction is safe."""
    bundle_id = insert_bundle()

    move(bundle_id, "experimental")

    assert status_of(bundle_id) == "experimental"


def test_a_status_outside_the_lifecycle_is_refused() -> None:
    with pytest.raises(IntegrityError, match="status_is_a_known_status"):
        insert_bundle(status="published")


# ---------------------------------------------------------------------------
# One production bundle per model name — spec §59
# ---------------------------------------------------------------------------


def test_a_second_production_bundle_for_one_name_is_refused() -> None:
    """ "Never overwrite production model files": the predecessor retires first,
    and the index holds the rule while both rows exist."""
    insert_bundle(status="production")
    challenger = insert_bundle(
        model_version="grading-psa-v0.2.0",
        artifact_location="model-bundles/grading-psa-v0.2.0/weights.pt",
        status="candidate",
    )

    with pytest.raises(IntegrityError, match="uq_model_bundles_one_production_per_name"):
        move(challenger, "production")


def test_two_models_may_each_hold_a_production_bundle() -> None:
    insert_bundle(status="production")
    insert_bundle(
        model_name="grading-tag",
        model_version="grading-tag-v0.1.0",
        artifact_location="model-bundles/grading-tag-v0.1.0/weights.pt",
        status="production",
    )


def test_a_retired_predecessor_frees_the_name() -> None:
    predecessor = insert_bundle(status="production")
    challenger = insert_bundle(
        model_version="grading-psa-v0.2.0",
        artifact_location="model-bundles/grading-psa-v0.2.0/weights.pt",
        status="candidate",
    )

    move(predecessor, "retired")
    move(challenger, "production")

    assert status_of(challenger) == "production"


# ---------------------------------------------------------------------------
# The reference into the datasets domain, and the identifier grammars
# ---------------------------------------------------------------------------


def test_a_referenced_dataset_version_cannot_be_deleted() -> None:
    """RESTRICT for #153's reason: a corpus a registered model trained on
    cannot be un-published."""
    insert_bundle()

    with pytest.raises(IntegrityError, match="fk_model_bundles_training_dataset_version"):
        execute(sa.delete(dataset_versions).where(dataset_versions.c.version == DATASET_VERSION))


def test_an_unpublished_dataset_version_is_refused() -> None:
    with pytest.raises(IntegrityError, match="fk_model_bundles_training_dataset_version"):
        insert_bundle(
            model_version="grading-psa-v0.9.0",
            training_dataset_version="pokemon-condition-v9.9.9",
        )


def test_a_version_outside_the_grammar_is_refused() -> None:
    with pytest.raises(IntegrityError, match="version_is_an_explicit_identifier"):
        insert_bundle(model_version="grading-psa-latest")


def test_a_version_naming_another_model_is_refused() -> None:
    with pytest.raises(IntegrityError, match="version_names_its_model"):
        insert_bundle(model_version="grading-tag-v0.1.0")


def test_a_version_extending_the_name_through_a_v_segment_is_refused() -> None:
    """'grading-vault-v1.0.0' does not belong to 'grading' — the CHECK anchors
    the whole version to the name, not merely its prefix."""
    with pytest.raises(IntegrityError, match="version_names_its_model"):
        insert_bundle(model_name="grading", model_version="grading-vault-v1.0.0")


def test_a_latest_location_is_unstorable() -> None:
    with pytest.raises(IntegrityError, match="location_never_says_latest"):
        insert_bundle(artifact_location="model-bundles/grading-psa/latest/weights.pt")


def test_a_blank_location_is_unstorable() -> None:
    with pytest.raises(IntegrityError, match="location_is_not_blank"):
        insert_bundle(artifact_location="   ")
