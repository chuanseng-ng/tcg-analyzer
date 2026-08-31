"""Run the condition analyzers over a dataset version and record the score — #188.

The scoring itself is `tcg_ml_evaluation`, which reads a manifest and domain
shapes and nothing else. This module is the runner: it renders the version's
manifest (the same bytes `datasets/manifests/` holds), resolves each member's
stored artifact and card frame, runs the four analyzers at default thresholds,
replays the public `compose` per physical copy, and writes one experiment
record into `ml/evaluation/experiments/` — spec §61's log as a committed file,
not a platform.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg_corpus
    export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
    uv run tcg-evaluate-condition --version pokemon-condition-v0.1.0

**This runs from the worker image only**, on
:mod:`tcg_api.datasets.normalization`'s terms: the analyzers bring OpenCV,
which the API image does not install, and
`services/api/tests/test_import_purity.py` keeps that true.

**Frames come from stored `normalization_details`** (#182's rule, via
`tcg_domain.condition.card_frame_of`) — never the normalizer's current
thresholds. A member with no stored artifact or no derivable frame is a
counted exclusion, and the photograph is never substituted (#159's rule).

**An experiment record is never overwritten.** A rerun at the same versions
belongs beside its predecessor only if something changed — and if something
changed, a version constant should have bumped, which changes the filename.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Final, NamedTuple

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine
from tcg_domain.analysis import ImageSide
from tcg_domain.annotation import CornerRegion, EdgeRegion
from tcg_domain.condition import BoundingBox, RegionFinding, SurfaceAssessment, card_frame_of
from tcg_domain.confidence import InsufficientInformation, Uncertain
from tcg_ml_centering import DEFAULT_CENTERING_THRESHOLDS, SideCentering, centering_of, measure
from tcg_ml_condition import CONDITION_VERSION, compose
from tcg_ml_corners import DEFAULT_CORNER_THRESHOLDS
from tcg_ml_corners import classify as classify_corners
from tcg_ml_edges import DEFAULT_EDGE_THRESHOLDS
from tcg_ml_edges import classify as classify_edges
from tcg_ml_evaluation import (
    EVALUATION_VERSION,
    ImagePredictions,
    PredictedCentering,
    evaluate,
    load_manifest,
)
from tcg_ml_surface import DEFAULT_SURFACE_THRESHOLDS
from tcg_ml_surface import classify as classify_surface
from tcg_shared.storage import StorageError, StorageKey
from tcg_shared.storage.port import ObjectStorage

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.tables import training_images
from tcg_api.datasets.versioning import read_manifest, render_manifest
from tcg_api.logging import configure_logging
from tcg_api.storage import create_object_storage

__all__ = [
    "EXPERIMENTS_DIR",
    "AnalyzerOutputs",
    "experiment_path",
    "main",
    "pair_copies",
    "predict_or_exclude",
    "render_experiment",
    "run",
    "write_experiment",
]

logger = logging.getLogger(__name__)

#: Where an experiment record lands — `MANIFESTS_DIR`'s walk, pointed at the
#: harness's own directory, because §61's log lives beside the code that
#: produced it and is committed the way a manifest is.
EXPERIMENTS_DIR: Final = Path(__file__).resolve().parents[5] / "ml" / "evaluation" / "experiments"

#: The four analyzers' default thresholds, merged — the same record
#: `tcg_api.analysis.condition` stores beside every worker document, restated
#: here because importing that module would pull §19's gate into a pass that
#: deliberately does not gate (`normalization.py`'s reasoning).
_THRESHOLDS_RECORD: Final[dict[str, float]] = {
    **DEFAULT_CENTERING_THRESHOLDS.as_record(),
    **DEFAULT_CORNER_THRESHOLDS.as_record(),
    **DEFAULT_EDGE_THRESHOLDS.as_record(),
    **DEFAULT_SURFACE_THRESHOLDS.as_record(),
}


def pair_copies(
    sides: Mapping[uuid.UUID, tuple[str, uuid.UUID | None]],
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Pair each physical copy's front and back for the `compose` replay.

    Takes ``{training_image_id: (side, physical_copy_id)}`` for the images
    whose analyzers ran. An image naming no copy has no siblings (#159's
    rule), so it composes nothing; so does a copy with only one side.
    """
    by_copy: dict[uuid.UUID, dict[str, uuid.UUID]] = {}
    for image_id, (side, copy_id) in sides.items():
        if copy_id is not None:
            # First-seen wins: the caller feeds this in id order, so a copy
            # with two images of one side composes the same pair on every run
            # rather than whichever row a query plan produced last.
            by_copy.setdefault(copy_id, {}).setdefault(side, image_id)
    return [
        (images["front"], images["back"])
        for _, images in sorted(by_copy.items(), key=lambda item: str(item[0]))
        if "front" in images and "back" in images
    ]


def experiment_path(version: str, directory: Path = EXPERIMENTS_DIR) -> Path:
    """`{dataset version}+{harness version}.json` — both inputs in the name."""
    return directory / f"{version}+{EVALUATION_VERSION}.json"


def render_experiment(report: Mapping[str, object], *, commit: str) -> str:
    """The §61 envelope around the scorer's report, rendered deterministically.

    dataset and metrics are the report's; the model is `CONDITION_VERSION`
    (the compose version plus all four analyzer versions); the
    hyperparameters are the merged default-threshold records. Hardware and
    training duration do not apply to a scored heuristic and are deliberately
    absent. The render rules are the manifest's: sorted keys, one trailing
    newline.
    """
    payload = {
        **report,
        "condition_version": CONDITION_VERSION,
        "analyzer_thresholds": _THRESHOLDS_RECORD,
        "git_commit": commit,
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_experiment(text: str, path: Path) -> Path:
    """Write one record, refusing to replace one that exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


async def score_version(
    engine: AsyncEngine, storage: ObjectStorage, *, version: str
) -> dict[str, object]:
    """Render the manifest, run the analyzers, and score the outputs."""
    async with engine.connect() as connection:
        manifest = await read_manifest(connection, version=version)
        rows = (
            await connection.execute(
                sa.select(
                    training_images.c.id,
                    training_images.c.side,
                    training_images.c.physical_copy_id,
                    training_images.c.normalized_uri,
                    training_images.c.normalization_details,
                )
                .where(
                    training_images.c.id.in_(
                        [member.training_image_id for member in manifest.members]
                    )
                )
                # `pair_copies` keeps the first image it sees per side, so this
                # order is what makes a duplicate-side copy compose the same
                # pair on every run.
                .order_by(training_images.c.id)
            )
        ).all()

    corpus = load_manifest(render_manifest(manifest))

    predictions: dict[uuid.UUID, ImagePredictions] = {}
    excluded: dict[uuid.UUID, str] = {}
    ran: dict[uuid.UUID, tuple[str, uuid.UUID | None]] = {}
    outputs: dict[uuid.UUID, AnalyzerOutputs] = {}
    for row in rows:
        if row.normalized_uri is None:
            excluded[row.id] = "no_normalized_artifact"
            continue
        frame = card_frame_of(row.normalization_details)
        if frame is None:
            excluded[row.id] = "no_card_frame"
            continue
        try:
            data = await storage.get(StorageKey(row.normalized_uri))
        except StorageError:
            logger.warning("training image %s: its stored artifact could not be read", row.id)
            excluded[row.id] = "stored_artifact_unreadable"
            continue

        answered = predict_or_exclude(
            row.id, data, frame, side=ImageSide(row.side), excluded=excluded
        )
        if answered is None:
            continue
        raw, scored = answered
        predictions[row.id] = scored
        outputs[row.id] = raw
        ran[row.id] = (row.side, row.physical_copy_id)

    composed = []
    for front_id, back_id in pair_copies(ran):
        front, back = outputs[front_id], outputs[back_id]
        composed.append(
            compose(
                centering=centering_of(front.centering, back.centering),
                corners={ImageSide.FRONT: front.corners, ImageSide.BACK: back.corners},
                edges={ImageSide.FRONT: front.edges, ImageSide.BACK: back.edges},
                surface={ImageSide.FRONT: front.surface, ImageSide.BACK: back.surface},
            )
        )

    return evaluate(corpus, predictions=predictions, composed=composed, excluded=excluded)


class AnalyzerOutputs(NamedTuple):
    """One artifact's four analyzer answers, in their own shapes.

    Kept beside the scorer's `ImagePredictions` view of the same run so the
    `compose` replay takes exactly what the analyzers said and nothing
    decodes the artifact twice.
    """

    centering: Uncertain[SideCentering]
    corners: Uncertain[Mapping[CornerRegion, RegionFinding]]
    edges: Uncertain[Mapping[EdgeRegion, RegionFinding]]
    surface: Uncertain[SurfaceAssessment]


def predict_or_exclude(
    image_id: uuid.UUID,
    data: bytes,
    frame: BoundingBox,
    *,
    side: ImageSide,
    excluded: dict[uuid.UUID, str],
) -> tuple[AnalyzerOutputs, ImagePredictions] | None:
    """Run the analyzers, containing a raise to the one image it came from.

    One bad artifact must cost one image, never the whole record — the
    analyzers answer their known failure modes as refusals, but this path
    decodes stored bytes and a surprise from the CV stack should land in the
    exclusion ledger beside the other per-image reasons.
    """
    try:
        return _predict(data, frame, side=side)
    except Exception:
        logger.exception("training image %s: an analyzer raised; excluded from the run", image_id)
        excluded[image_id] = "analyzer_error"
        return None


def _predict(
    data: bytes, frame: BoundingBox, *, side: ImageSide
) -> tuple[AnalyzerOutputs, ImagePredictions]:
    """Run the four analyzers over one artifact at default thresholds."""
    centering = measure(data, card_frame=frame)
    corners = classify_corners(data, card_frame=frame)
    edges = classify_edges(data, card_frame=frame)
    surface = classify_surface(data, side=side, card_frame=frame)
    scored = ImagePredictions(
        centering=(
            PredictedCentering(
                horizontal=centering.horizontal,
                vertical=centering.vertical,
                confidence=centering.confidence.value,
            )
            if not isinstance(centering, InsufficientInformation)
            else centering
        ),
        corners=corners,
        edges=edges,
        surface=surface,
    )
    return AnalyzerOutputs(centering, corners, edges, surface), scored


def _git_commit(fallback: str | None) -> str:
    """The commit the run scored — `git rev-parse`, or `--commit` where there
    is no checkout (the worker container)."""
    git = shutil.which("git")
    try:
        if git is None:
            raise OSError("git is not on PATH")
        result = subprocess.run(  # noqa: S603 — a fixed argv, no shell
            [git, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[5],
        )
    except (OSError, subprocess.CalledProcessError):
        if fallback is None:
            raise SystemExit(
                "not a git checkout and no --commit given; spec §61 requires the "
                "commit on every experiment record"
            ) from None
        return fallback
    return result.stdout.strip()


async def run(*, version: str, output: Path | None, commit: str | None) -> Path:
    settings = get_settings()
    storage = create_object_storage(settings)
    engine = create_engine(settings)
    try:
        report = await score_version(engine, storage, version=version)
    finally:
        await engine.dispose()

    text = render_experiment(report, commit=_git_commit(commit))
    path = write_experiment(text, output or experiment_path(version))
    logger.info("experiment record written to %s", path)
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="the dataset version to score")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="where to write the record (default: ml/evaluation/experiments/)",
    )
    parser.add_argument(
        "--commit",
        default=None,
        help="the git commit to record when the working directory is not a checkout",
    )
    return parser


def main() -> int:
    """Console-script entry point (`uv run tcg-evaluate-condition`)."""
    arguments = _parser().parse_args()
    configure_logging(get_settings())
    asyncio.run(run(version=arguments.version, output=arguments.output, commit=arguments.commit))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
