"""Score the condition analyzers against a dataset version — #188, spec §25 and §26.

A manifest's members in, per-axis metrics out. The runner
(`tcg-evaluate-condition`, in `services/api`) resolves bytes and card frames
and runs the analyzers; this package holds the truth protocol, the match
rule, the metrics and the calibration arithmetic, and never touches OpenCV,
the database or object storage.
"""

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
    "EvaluationCorpus",
    "ImagePredictions",
    "PredictedCentering",
    "evaluate",
    "load_manifest",
]
