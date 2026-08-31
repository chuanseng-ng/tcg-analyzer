"""The model-bundle registration path — #189's library and console script.

The pure half runs everywhere: `verify_registration` mirrors the CHECKs so the
operator gets a sentence naming the rule rather than a constraint name — the
Python check is the message, the constraint is the guarantee, exactly as
`verify_provenance` relates to ADR 0008's gate. The integration half writes a
real row and reads it back.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.datasets.tables import dataset_versions
from tcg_api.models.registration import (
    ModelRegistrationError,
    _parser,
    _validated,
    load_document,
    register_model_bundle,
    verify_registration,
)
from tcg_api.models.tables import model_bundles

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

#: A registration every rule accepts. Each test below spoils exactly one field,
#: so a failure names the rule that was broken.
LEGAL: dict[str, Any] = {
    "model_name": "grading-psa",
    "model_version": "grading-psa-v0.1.0",
    "training_dataset_version": "pokemon-condition-v0.1.0",
    "artifact_location": "model-bundles/grading-psa-v0.1.0/weights.pt",
}


def verify(**overrides: Any) -> None:
    verify_registration(**{**LEGAL, **overrides})


# ---------------------------------------------------------------------------
# The message half — one sentence per rule, before the constraint speaks
# ---------------------------------------------------------------------------


def test_a_legal_registration_passes() -> None:
    verify()


def test_a_name_outside_the_slug_grammar_is_refused() -> None:
    with pytest.raises(ModelRegistrationError, match="model_name"):
        verify(model_name="Grading-PSA")


def test_a_version_outside_the_grammar_is_refused() -> None:
    with pytest.raises(ModelRegistrationError, match="model_version"):
        verify(model_version="grading-psa-0.1.0")


def test_a_latest_version_is_refused_as_a_moving_pointer() -> None:
    """The wrong idea, not merely the wrong spelling — `CardDatabaseVersion`'s
    precedent, refused before the grammar gets to call it malformed."""
    with pytest.raises(ModelRegistrationError, match="moving pointer"):
        verify(model_version="grading-psa-latest")


def test_a_version_naming_another_model_is_refused() -> None:
    with pytest.raises(ModelRegistrationError, match="grading-tag"):
        verify(model_version="grading-tag-v0.1.0")


def test_a_version_extending_the_name_through_a_v_segment_is_refused() -> None:
    """'grading-vault-v1.0.0' does not belong to 'grading' — a starts-with
    check would file it there, and the one-production rule would then scope
    the wrong family."""
    with pytest.raises(ModelRegistrationError, match="does not name model"):
        verify(model_name="grading", model_version="grading-vault-v1.0.0")


def test_a_dataset_version_outside_the_grammar_is_refused() -> None:
    with pytest.raises(ModelRegistrationError, match="training_dataset_version"):
        verify(training_dataset_version="pokemon-condition")


def test_a_latest_dataset_version_is_refused_as_a_moving_pointer() -> None:
    with pytest.raises(ModelRegistrationError, match="moving pointer"):
        verify(training_dataset_version="pokemon-condition-latest")


def test_a_blank_location_is_refused() -> None:
    with pytest.raises(ModelRegistrationError, match="artifact_location"):
        verify(artifact_location="   ")


def test_a_latest_location_is_refused_as_a_moving_pointer() -> None:
    with pytest.raises(ModelRegistrationError, match="moving pointer"):
        verify(artifact_location="model-bundles/grading-psa/latest/weights.pt")


# ---------------------------------------------------------------------------
# The documents — a config and a metrics file are JSON objects, or refused
# ---------------------------------------------------------------------------


def test_a_json_object_loads(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"epochs": 40}), encoding="utf-8")

    assert load_document(path) == {"epochs": 40}


def test_a_json_array_is_refused(tmp_path: Path) -> None:
    """A registry row records *the* configuration; a list is some other shape."""
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ModelRegistrationError, match="JSON object"):
        load_document(path)


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("epochs: 40", encoding="utf-8")

    with pytest.raises(ModelRegistrationError, match="not JSON"):
        load_document(path)


# ---------------------------------------------------------------------------
# The console script's argument contract
# ---------------------------------------------------------------------------


def test_every_flag_is_required() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args([])


def test_a_document_that_is_not_a_file_is_refused(tmp_path: Path) -> None:
    parser = _parser()
    arguments = parser.parse_args(
        [
            "--model-name",
            "grading-psa",
            "--model-version",
            "grading-psa-v0.1.0",
            "--training-dataset-version",
            "pokemon-condition-v0.1.0",
            "--training-config",
            str(tmp_path / "missing.json"),
            "--metrics",
            str(tmp_path / "missing.json"),
            "--artifact-location",
            "model-bundles/grading-psa-v0.1.0/weights.pt",
        ]
    )

    with pytest.raises(SystemExit):
        _validated(parser, arguments)


# ---------------------------------------------------------------------------
# The guarantee half — a real row, and the database's own refusals
# ---------------------------------------------------------------------------


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` leaves the database at `base`, so bring it up here."""
    if not DATABASE_URL:
        return
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture(autouse=True)
def empty_tables() -> Iterator[None]:
    if not DATABASE_URL:
        yield
        return
    execute(sa.text("TRUNCATE model_bundles, dataset_versions RESTART IDENTITY CASCADE"))
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

    run(scenario)


def fetch(statement: Any) -> list[Any]:
    async def scenario() -> list[Any]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return list((await connection.execute(statement)).all())
        finally:
            await engine.dispose()

    return run(scenario)


def insert_dataset_version(version: str = "pokemon-condition-v0.1.0") -> None:
    execute(
        sa.insert(dataset_versions),
        {"id": uuid.uuid4(), "version": version, "split_seed": 1},
    )


def register(**overrides: Any) -> uuid.UUID:
    async def scenario() -> uuid.UUID:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                return await register_model_bundle(
                    connection,
                    training_config={"epochs": 40},
                    metrics={"validation": {"macro_f1": 0.5}},
                    **{**LEGAL, **overrides},
                )
        finally:
            await engine.dispose()

    return run(scenario)


@pytest.mark.integration
@requires_postgres
def test_a_registered_bundle_is_experimental() -> None:
    """Every bundle is born at the foot of the lifecycle; promotion is a later,
    deliberate act — there is no flag to register a production bundle."""
    insert_dataset_version()

    bundle_id = register()
    rows = fetch(sa.select(model_bundles).where(model_bundles.c.id == bundle_id))

    assert len(rows) == 1
    assert rows[0].status == "experimental"
    assert rows[0].model_version == "grading-psa-v0.1.0"
    assert rows[0].training_config == {"epochs": 40}


@pytest.mark.integration
@requires_postgres
def test_the_same_version_twice_is_refused() -> None:
    insert_dataset_version()
    register()

    with pytest.raises(IntegrityError, match="uq_model_bundles_model_version"):
        register()


@pytest.mark.integration
@requires_postgres
def test_an_unpublished_dataset_version_is_refused() -> None:
    """The FK is the guarantee that a bundle's corpus resolves — nothing is
    registered against a version that was never published."""
    with pytest.raises(IntegrityError, match="fk_model_bundles_training_dataset_version"):
        register()
