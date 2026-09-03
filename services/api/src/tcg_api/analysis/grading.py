"""Running the grade prediction step — issue #227, spec §24, §63, ADR 0011.

The models themselves are `tcg_ml_grading_{psa,tag,bgs}`, which know nothing
about databases. This module is the wiring, `condition.py`'s twin one stage
downstream: read the condition document that step stored (**never the
analyzers** — #187's seam), rehydrate the assessment, hand it to each
company's adapter with that company's model injected (#226), and write one
document onto the analysis.

**The predicting adapters are built here and nowhere else.** `ADAPTERS` in
`packages/grading-companies` is constructed without a model and refuses,
which is what keeps the API image — it imports that registry through
`routers/grading.py` — free of any predictor. `PREDICTING_ADAPTERS` is the
same three classes keyed by the same slugs, each given its model at
construction, so a fourth company is still one adapter and no caller change
(ADR 0011 decision 5).

**All three companies, at the claim.** No economic configuration exists yet
when the worker runs — it is posted later, while the analysis is
`analyzing` — so nothing has been selected; the results route selects among
the stored predictions on read. It may run before the card is confirmed for
the reason the condition step may: the input is the neutral representation
and no predictor reads the card's identity (§20 governs economics, and a
grade distribution is not one).

**A step that runs always writes a document.** Per company, either the full
distribution with the model's confidence and version, or the one-key
``insufficient_information`` object wearing its reason — a model's own
refusal (#226 returns it), or the condition document's, propagated to every
company because the only refusal a predictor makes is a refusal on the way in
(ADR 0011 decision 1). ``analyses.grade_predictions`` staying NULL therefore
keeps exactly one meaning: the step never ran.

**Nothing imports this module eagerly, and that is load-bearing** — the same
rule and guard as `condition.py`, for a narrower reason: these packages bind
no OpenCV, but `tests/test_import_purity.py` probes the ``tcg_ml_`` prefix on
the request path and they match it. `jobs._advance` imports this file inside
the function.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from types import MappingProxyType
from typing import Final
from uuid import UUID

import anyio.to_thread
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.condition import ConditionAssessment
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain
from tcg_domain.distribution import GradeDistribution
from tcg_grading_companies import (
    BGSAdapter,
    GradePrediction,
    GradePredictionFailed,
    GradingCompany,
    GradingCompanyAdapter,
    PSAAdapter,
    TAGAdapter,
)
from tcg_ml_grading_bgs import DEFAULT_BGS_GRADING_THRESHOLDS, GRADING_BGS_VERSION
from tcg_ml_grading_bgs import predict as predict_bgs
from tcg_ml_grading_psa import DEFAULT_PSA_GRADING_THRESHOLDS, GRADING_PSA_VERSION
from tcg_ml_grading_psa import predict as predict_psa
from tcg_ml_grading_tag import DEFAULT_TAG_GRADING_THRESHOLDS, GRADING_TAG_VERSION
from tcg_ml_grading_tag import predict as predict_tag

from tcg_api.analysis.sessions import read_condition, record_grade_predictions

__all__ = ["GRADING_VERSION", "PREDICTING_ADAPTERS", "predict_grades"]

logger = structlog.get_logger(__name__)

#: The three per-company versions composed into one, slug order —
#: `CONDITION_VERSION`'s ``+``-joined rule, so a package bump cannot be
#: forgotten. Composed into `analyses.model_bundle_version` after the
#: condition version (ADR 0011 decision 6).
GRADING_VERSION: Final = "+".join((GRADING_BGS_VERSION, GRADING_PSA_VERSION, GRADING_TAG_VERSION))

#: `ADAPTERS` with the models supplied — the same three classes, the same
#: slugs. A test asserts the key sets match, so a fourth company registered
#: there without a model here fails loudly rather than silently unpredicted.
PREDICTING_ADAPTERS: Final[Mapping[str, GradingCompanyAdapter]] = MappingProxyType(
    {
        str(GradingCompany.BGS): BGSAdapter(predictor=predict_bgs),
        str(GradingCompany.PSA): PSAAdapter(predictor=predict_psa),
        str(GradingCompany.TAG): TAGAdapter(predictor=predict_tag),
    }
)

#: The three predictors' thresholds, merged into the one record stored beside
#: every document — `condition.py`'s pattern; each ``as_record()`` prefixes
#: its keys with its package name, so the merge cannot collide.
_THRESHOLDS_RECORD: Final[dict[str, float]] = {
    **DEFAULT_PSA_GRADING_THRESHOLDS.as_record(),
    **DEFAULT_TAG_GRADING_THRESHOLDS.as_record(),
    **DEFAULT_BGS_GRADING_THRESHOLDS.as_record(),
}

#: The reason stored when there is no condition document to read at all —
#: a caller that skipped the condition step. Part of the stored vocabulary,
#: like #187's reasons.
CONDITION_STEP_NOT_RUN: Final = "condition_step_not_run"


async def predict_grades(db: AsyncSession, analysis_id: UUID) -> None:
    """Predict `analysis_id`'s grade per company from its stored condition, and record it.

    Does not commit — the caller owns the transaction, so the document lands
    with the transition it precedes or not at all. Never raises over what the
    condition *shows*: a refused assessment is three recorded refusals (spec
    §2.7), not a job failure.

    Raises:
        GradingCompanyError: If a model broke, or handed back something that
            is not a prediction. Left to propagate, `assess_condition`'s rule
            for a storage failure: a model that broke must never be recorded
            as a model that declined, and the job runner's retry path is the
            right place for it.
        InvalidConditionAssessment: If the stored document no longer matches
            the domain's writer. Same reasoning — a corrupt record is a
            failure, not a refusal.
    """
    document = await read_condition(db, analysis_id)
    if document is None:
        await _record(db, analysis_id, InsufficientInformation(CONDITION_STEP_NOT_RUN))
        return
    if "assessment" not in document:
        reason = document.get("insufficient_information")
        await _record(db, analysis_id, InsufficientInformation(_reason_of(reason)))
        return

    assessment = ConditionAssessment.from_record(document["assessment"])  # type: ignore[arg-type]
    # Off the event loop, `assess_condition`'s rule — three mappings over one
    # assessment, in one hop rather than three.
    answers = await anyio.to_thread.run_sync(partial(_predict_all, assessment))
    await _record(db, analysis_id, answers)


def _reason_of(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _predict_all(assessment: ConditionAssessment) -> dict[str, Uncertain[GradePrediction]]:
    return {
        slug: _checked(slug, PREDICTING_ADAPTERS[slug].predict_grade(assessment))
        for slug in sorted(PREDICTING_ADAPTERS)
    }


def _checked(slug: str, answer: Uncertain[GradePrediction]) -> Uncertain[GradePrediction]:
    """Spec §63's boundary: nothing but a real prediction reaches the store.

    `GradeDistribution`'s constructor *is* §63 and the adapter has already
    checked the ladder (#226). What neither can see is a swapped model handing
    back a `GradePrediction` whose fields are not those types at all — a
    dataclass does not check — so that is the one thing refused here, as the
    model's failure it is.
    """
    if isinstance(answer, InsufficientInformation):
        return answer
    if not isinstance(answer.grade_probability, GradeDistribution) or not isinstance(
        answer.model_confidence, Confidence
    ):
        raise GradePredictionFailed(
            f"the {slug} model answered something that is not a grade prediction "
            f"(spec §63): {type(answer.grade_probability).__name__}, "
            f"{type(answer.model_confidence).__name__}"
        )
    return answer


def _entry(answer: Uncertain[GradePrediction]) -> dict[str, object]:
    if isinstance(answer, InsufficientInformation):
        return {"insufficient_information": answer.reason}
    return {
        # `as_mapping()` is `{str(grade): probability}`, sorted — what
        # `GradeDistribution.from_mapping` reads straight back.
        "distribution": answer.grade_probability.as_mapping(),
        "model_confidence": answer.model_confidence.value,
        "model_version": answer.model_version,
    }


async def _record(
    db: AsyncSession,
    analysis_id: UUID,
    outcome: InsufficientInformation | Mapping[str, Uncertain[GradePrediction]],
) -> None:
    """Write the document — version and thresholds first, then the predictions.

    One writer for both outcomes so the two shapes cannot drift, and one log
    event for the same reason (`condition.py`'s pattern). The identifier, the
    version and which companies refused with what reason — never a
    probability (spec §54).
    """
    answers: Mapping[str, Uncertain[GradePrediction]] = (
        dict.fromkeys(sorted(PREDICTING_ADAPTERS), outcome)
        if isinstance(outcome, InsufficientInformation)
        else outcome
    )
    document: dict[str, object] = {
        "version": GRADING_VERSION,
        "thresholds": _THRESHOLDS_RECORD,
        "predictions": {slug: _entry(answer) for slug, answer in answers.items()},
    }
    await record_grade_predictions(db, analysis_id, details=document)
    refused = {
        slug: answer.reason
        for slug, answer in answers.items()
        if isinstance(answer, InsufficientInformation)
    }
    logger.info(
        "analysis.grades_predicted",
        analysis_id=str(analysis_id),
        version=GRADING_VERSION,
        refused=sorted(refused),
        reasons=refused,
    )
