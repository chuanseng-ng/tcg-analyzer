"""Score the ML system against a dataset version — spec §25, §26 and §27.

Two harnesses, separately versioned because they score different things from
different inputs:

* :func:`~tcg_ml_evaluation.report.evaluate` scores M7's condition analyzers
  against the manifest's annotation rows (#188).
* :func:`~tcg_ml_evaluation.grading.evaluate_grades` scores M8's per-company
  grade distributions against the grades companies issued (#222).

Both take the model outputs from their caller. The runner
(`tcg-evaluate-condition`, in `services/api`) resolves bytes and card frames
and runs the analyzers; this package holds the truth protocol, the match rule,
the metrics and the calibration arithmetic, and never touches OpenCV, the
database or object storage.
"""

from tcg_ml_evaluation.grading import (
    GRADE_EVALUATION_VERSION,
    WILSON_Z_95,
    WITHIN_ONE_TARGET,
    GradeSubject,
    IssuedGrade,
    evaluate_grades,
)
from tcg_ml_evaluation.manifest import EvaluationCorpus, load_manifest
from tcg_ml_evaluation.report import (
    CENTERING_AGREEMENT_TOLERANCE,
    EVALUATION_VERSION,
    ImagePredictions,
    PredictedCentering,
    evaluate,
)

__all__ = [
    "CENTERING_AGREEMENT_TOLERANCE",
    "EVALUATION_VERSION",
    "GRADE_EVALUATION_VERSION",
    "WILSON_Z_95",
    "WITHIN_ONE_TARGET",
    "EvaluationCorpus",
    "GradeSubject",
    "ImagePredictions",
    "IssuedGrade",
    "PredictedCentering",
    "evaluate",
    "evaluate_grades",
    "load_manifest",
]
