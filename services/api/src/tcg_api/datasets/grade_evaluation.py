"""Run the three grade predictors over a dataset version and record the score — #242.

The scoring is `tcg_ml_evaluation.grading.evaluate_grades` (#222), which
takes predictions *and* issued grades from its caller and reads neither the
database nor the analyzers. This module is the caller: it runs
:func:`tcg_api.datasets.evaluation.analyze_version` (the manifest rendered,
the four analyzers at default thresholds), replays the public `compose` per
physical copy, hands the assessment to each company's predicting adapter,
fills the copy's issued grades from its manifest member, and writes one
experiment record into `ml/evaluation/experiments/` — ADR 0011's third
closing condition for M8, as a committed file.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg_corpus
    export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
    uv run tcg-evaluate-grading --version pokemon-condition-v0.2.0

**This runs from the worker image only**, for `tcg-evaluate-condition`'s
reason: reaching a `ConditionAssessment` means running the analyzers, which
bring OpenCV.

**Every physical copy in the version is a subject.** A copy that could not
be composed — a side excluded before the analyzers ran, a side missing from
the version, or the composer's own refusal — is predicted by nobody and
carries that reason as all three companies' prediction, in the worker's
vocabulary (`no_card_frame_for_back`): ADR 0011 decision 1 puts the only
refusal on the way in, and the scorer's `abstained` ledger counts it. A
fabricated assessment never reaches a model.

**Sides are paired through the database.** The manifest deliberately carries
no `physical_copy_id` (#220); `analyze_version` reads it beside each row.

**An experiment record is never overwritten** — `evaluation.py`'s rule and
its writer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from sqlalchemy.ext.asyncio import AsyncEngine
from tcg_domain.condition import ConditionAssessment
from tcg_domain.confidence import InsufficientInformation, Uncertain
from tcg_grading_companies import GradePrediction, GradeScale, GradingCompanyError
from tcg_ml_condition import CONDITION_VERSION
from tcg_ml_evaluation.grading import GRADE_EVALUATION_VERSION, GradeSubject, evaluate_grades
from tcg_ml_evaluation.truth import issued_grades
from tcg_shared.storage.port import ObjectStorage

from tcg_api.analysis.grading import GRADING_VERSION, PREDICTING_ADAPTERS, PREDICTOR_THRESHOLDS
from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.evaluation import (
    ANALYZER_THRESHOLDS,
    AnalyzedVersion,
    analyze_version,
    compose_pair,
    experiment_path,
    git_commit,
    write_experiment,
)
from tcg_api.logging import configure_logging
from tcg_api.storage import create_object_storage

__all__ = [
    "SCALES",
    "condition_of",
    "main",
    "predict_or_refuse",
    "record_path",
    "render_experiment",
    "run",
    "subjects_of",
]

logger = logging.getLogger(__name__)

#: The ladder each company is scored against — `evaluate_grades` takes it as
#: a parameter rather than reaching into the registry itself (#222), so it is
#: read off the same adapters that predict.
SCALES: Final[Mapping[str, GradeScale]] = {
    slug: adapter.get_grade_scale() for slug, adapter in PREDICTING_ADAPTERS.items()
}


def record_path(version: str) -> Path:
    """`{dataset version}+{GRADE_EVALUATION_VERSION}.json` — the grade family."""
    return experiment_path(version, harness=GRADE_EVALUATION_VERSION)


def render_experiment(report: Mapping[str, object], *, commit: str) -> str:
    """The §61 envelope around the scorer's report, rendered deterministically.

    Full provenance: the predictions were reached through the four analyzers
    and `compose`, then the three predictors, and this run writes no worker
    document — so every constant on that path is named here or nowhere.
    """
    payload = {
        **report,
        "condition_version": CONDITION_VERSION,
        "analyzer_thresholds": ANALYZER_THRESHOLDS,
        "grading_version": GRADING_VERSION,
        "predictor_thresholds": PREDICTOR_THRESHOLDS,
        "git_commit": commit,
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def condition_of(
    images: Mapping[str, uuid.UUID], analyzed: AnalyzedVersion
) -> Uncertain[ConditionAssessment]:
    """One copy's condition, or the reason it has none — in the worker's vocabulary."""
    for side in ("front", "back"):
        if side not in images:
            return InsufficientInformation(f"no_{side}_image")
        if images[side] in analyzed.excluded:
            return InsufficientInformation(f"{analyzed.excluded[images[side]]}_for_{side}")
    return compose_pair(analyzed.outputs[images["front"]], analyzed.outputs[images["back"]])


def predict_or_refuse(slug: str, assessment: ConditionAssessment) -> Uncertain[GradePrediction]:
    """One company's answer, containing a model's raise to that company.

    The adapter translates its model's exception into `GradePredictionFailed`
    with the cause chained (#226); one broken model must cost one company's
    column, never the record.
    """
    try:
        return PREDICTING_ADAPTERS[slug].predict_grade(assessment)
    except GradingCompanyError:
        logger.exception("%s: the predictor raised; counted as a refusal", slug)
        return InsufficientInformation("predictor_error")


def subjects_of(analyzed: AnalyzedVersion) -> list[GradeSubject]:
    """One `GradeSubject` per physical copy, in copy-id order.

    The split and the issued grades come off the copy's manifest member —
    `outcomes` is filled from `issued_grades(member)` and nowhere else (#220).
    Both sides share a split by construction (the splitter groups on the copy),
    and an image naming no copy is not a copy.
    """
    members = {member.training_image_id: member for member in analyzed.corpus.members}
    by_copy: dict[uuid.UUID, dict[str, uuid.UUID]] = {}
    for image_id, (side, copy_id) in analyzed.sides.items():
        if copy_id is None:
            logger.warning("training image %s names no physical copy; not a subject", image_id)
            continue
        # First-seen wins, on `pair_copies`' rule: the rows arrive in id order.
        by_copy.setdefault(copy_id, {}).setdefault(side, image_id)

    subjects = []
    for copy_id, images in sorted(by_copy.items(), key=lambda item: str(item[0])):
        sides = [members[image_id] for image_id in images.values()]
        if len({member.split for member in sides}) != 1:
            raise ValueError(f"physical copy {copy_id}: its sides sit in different splits")
        condition = condition_of(images, analyzed)
        predictions: dict[str, Uncertain[GradePrediction]] = {
            slug: (
                condition
                if isinstance(condition, InsufficientInformation)
                else predict_or_refuse(slug, condition)
            )
            for slug in sorted(PREDICTING_ADAPTERS)
        }
        subjects.append(
            GradeSubject(
                subject_id=copy_id,
                split=sides[0].split,
                predictions=predictions,
                outcomes=issued_grades(sides[0]),
            )
        )
    return subjects


async def score_version(
    engine: AsyncEngine, storage: ObjectStorage, *, version: str
) -> dict[str, object]:
    """Render the manifest, run the analyzers, predict per copy, and score."""
    analyzed = await analyze_version(engine, storage, version=version)
    return evaluate_grades(
        subjects_of(analyzed),
        dataset_version=analyzed.corpus.dataset_version,
        split_seed=analyzed.corpus.split_seed,
        scales=SCALES,
    )


async def run(*, version: str, output: Path | None, commit: str | None) -> Path:
    settings = get_settings()
    storage = create_object_storage(settings)
    engine = create_engine(settings)
    try:
        report = await score_version(engine, storage, version=version)
    finally:
        await engine.dispose()

    text = render_experiment(report, commit=git_commit(commit))
    path = write_experiment(text, output or record_path(version))
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
    """Console-script entry point (`uv run tcg-evaluate-grading`)."""
    arguments = _parser().parse_args()
    configure_logging(get_settings())
    asyncio.run(run(version=arguments.version, output=arguments.output, commit=arguments.commit))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
