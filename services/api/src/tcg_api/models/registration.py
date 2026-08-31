"""Register one trained model bundle in spec §58's registry.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    uv run tcg-register-model-bundle \\
        --model-name grading-psa \\
        --model-version grading-psa-v0.2.0 \\
        --training-dataset-version pokemon-condition-v0.1.0 \\
        --training-config config.json \\
        --metrics metrics.json \\
        --artifact-location model-bundles/grading-psa-v0.2.0/weights.pt

One invocation registers one bundle, and every bundle is born `experimental` —
there is deliberately no `--status`: promotion to `candidate` or `production`
is a later, separate act (M8's, when the first artifact needs promoting), and
a registration that could name its own state would let a bundle skip the foot
of §58's lifecycle. Until then a transition is an `UPDATE model_bundles SET
status = ...` by hand; the triggers hold the walk forward either way.

`verify_registration` mirrors the table's CHECK constraints so the operator
gets a sentence naming the rule rather than a constraint name — the Python
check is the message, the constraint is the guarantee, exactly as
`verify_provenance` relates to ADR 0008's gate. The artifact's existence in
object storage is deliberately not verified: nothing trained exists yet to
verify against, and a check invented now would encode a guess about how M8
lays out a bundle. The registry stores the reference; the store holds the
bytes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from tcg_domain import VERSION_PATTERN

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.logging import configure_logging
from tcg_api.models.tables import ModelStatus, model_bundles

__all__ = [
    "ModelRegistrationError",
    "load_document",
    "main",
    "register_model_bundle",
    "verify_registration",
]

logger = logging.getLogger(__name__)

_NAME_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_VERSION_UNIQUE: Final = "uq_model_bundles_model_version"
_DATASET_FK: Final = "fk_model_bundles_training_dataset_version_dataset_versions"


class ModelRegistrationError(ValueError):
    """A registration the registry's own rules refuse, named in a sentence."""


def verify_registration(
    *,
    model_name: str,
    model_version: str,
    training_dataset_version: str,
    artifact_location: str,
) -> None:
    """Say in words what the CHECK constraints would refuse in names.

    The rules are the table's, restated rather than replaced — a registration
    that slipped past this still meets the constraints, which is the guarantee.

    Raises:
        ModelRegistrationError: Naming the first rule the registration breaks.
    """
    if not _NAME_PATTERN.match(model_name):
        raise ModelRegistrationError(
            f"model_name must be a lowercase slug like 'grading-psa', got {model_name!r}"
        )
    _verify_version("model_version", model_version, example="grading-psa-v0.2.0")
    # Anchored to the whole version, exactly as the CHECK is: a starts-with
    # test would file 'grading-vault-v1.0.0' under 'grading'.
    if re.fullmatch(rf"{re.escape(model_name)}-v\d+\.\d+\.\d+", model_version) is None:
        raise ModelRegistrationError(
            f"model_version {model_version!r} does not name model {model_name!r}: "
            f"it must be '{model_name}-vX.Y.Z'"
        )
    _verify_version(
        "training_dataset_version", training_dataset_version, example="pokemon-condition-v0.1.0"
    )
    if not artifact_location.strip():
        raise ModelRegistrationError(
            "artifact_location is blank; the registry stores a reference, and a blank "
            "one references nothing"
        )
    if "latest" in artifact_location:
        raise ModelRegistrationError(
            f"artifact_location must pin one immutable artifact, not a moving pointer: "
            f"{artifact_location!r}. Spec §59: never overwrite production model files."
        )


def _verify_version(field: str, value: str, *, example: str) -> None:
    # A moving pointer is not a malformed identifier, it is the wrong idea —
    # `CardDatabaseVersion`'s precedent, refused before the grammar speaks.
    if "latest" in value:
        raise ModelRegistrationError(
            f"{field} must name one immutable version, not a moving pointer: {value!r}. "
            "Spec §59: a bundle is versioned explicitly, never referenced as '/latest/'."
        )
    if not VERSION_PATTERN.match(value):
        raise ModelRegistrationError(f"{field} must look like {example!r}, got {value!r}")


def load_document(path: Path) -> dict[str, object]:
    """One JSON object from disk — a training configuration or a metrics record.

    Raises:
        ModelRegistrationError: If the file is not JSON, or is JSON of some
            other shape. A registry row records *the* configuration and *the*
            metrics; a list or a bare string is some other document.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ModelRegistrationError(f"{path} is not JSON: {error}") from error
    if not isinstance(document, dict):
        raise ModelRegistrationError(
            f"{path} must hold a JSON object, not {type(document).__name__}"
        )
    return document


async def register_model_bundle(
    connection: AsyncConnection,
    *,
    model_name: str,
    model_version: str,
    training_dataset_version: str,
    training_config: Mapping[str, object],
    metrics: Mapping[str, object],
    artifact_location: str,
) -> uuid.UUID:
    """Write one bundle row, born `experimental`, and return its identifier.

    Raises:
        ModelRegistrationError: If `verify_registration` refuses it.
        IntegrityError: If the version is already registered
            (`uq_model_bundles_model_version`) or names a dataset version that
            was never published (`fk_model_bundles_..._dataset_versions`).
    """
    verify_registration(
        model_name=model_name,
        model_version=model_version,
        training_dataset_version=training_dataset_version,
        artifact_location=artifact_location,
    )
    bundle_id = uuid.uuid4()
    await connection.execute(
        sa.insert(model_bundles).values(
            id=bundle_id,
            model_name=model_name,
            model_version=model_version,
            training_dataset_version=training_dataset_version,
            training_config=dict(training_config),
            metrics=dict(metrics),
            artifact_location=artifact_location,
            status=ModelStatus.EXPERIMENTAL.value,
        )
    )
    return bundle_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument("--model-name", required=True, help="the model family — 'grading-psa'")
    parser.add_argument(
        "--model-version",
        required=True,
        help="the full immutable identifier — 'grading-psa-v0.2.0'",
    )
    parser.add_argument(
        "--training-dataset-version",
        required=True,
        help="the published dataset version the bundle trained on",
    )
    parser.add_argument(
        "--training-config",
        required=True,
        type=Path,
        help="a JSON object holding the configuration the run is reproducible through (§60)",
    )
    parser.add_argument(
        "--metrics",
        required=True,
        type=Path,
        help="a JSON object holding the evaluation metrics, in #188's per-split shape",
    )
    parser.add_argument(
        "--artifact-location",
        required=True,
        help="the object-storage key the bundle's bytes live under",
    )
    return parser


def _validated(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    for flag in ("training_config", "metrics"):
        path = getattr(arguments, flag)
        if not path.is_file():
            parser.error(f"--{flag.replace('_', '-')} names {path}, which is not a file")


async def run(arguments: argparse.Namespace) -> uuid.UUID:
    """Register the bundle the arguments describe, in one transaction."""
    training_config = load_document(arguments.training_config)
    metrics = load_document(arguments.metrics)

    engine = create_engine()
    try:
        async with engine.begin() as connection:
            return await register_model_bundle(
                connection,
                model_name=arguments.model_name,
                model_version=arguments.model_version,
                training_dataset_version=arguments.training_dataset_version,
                training_config=training_config,
                metrics=metrics,
                artifact_location=arguments.artifact_location,
            )
    finally:
        await engine.dispose()


def main() -> int:
    """Console-script entry point (`uv run tcg-register-model-bundle`)."""
    parser = _parser()
    arguments = parser.parse_args()
    _validated(parser, arguments)

    configure_logging(get_settings())

    try:
        bundle_id = asyncio.run(run(arguments))
    except ModelRegistrationError as refusal:
        logger.error("model bundle refused: %s", refusal)
        return 1
    except IntegrityError as conflict:
        if _VERSION_UNIQUE in str(conflict.orig):
            logger.error(
                "%s is already registered; a bundle is immutable, so register "
                "a new version rather than re-registering this one",
                arguments.model_version,
            )
        elif _DATASET_FK in str(conflict.orig):
            logger.error(
                "%s names no published dataset version; publish the corpus with "
                "tcg-publish-dataset-version first",
                arguments.training_dataset_version,
            )
        else:
            logger.error("model bundle refused by the database: %s", conflict.orig)
        return 1

    logger.info("registered %s as experimental bundle %s", arguments.model_version, bundle_id)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
