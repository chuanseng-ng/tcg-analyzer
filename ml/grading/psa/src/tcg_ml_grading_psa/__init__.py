"""PSA grade prediction — spec §24, issue #223.

The first of M8's three per-company models: M7's neutral condition
representation in, a probability distribution over PSA's own eighteen-grade
ladder out. Usage::

    from tcg_ml_grading_psa import predict

    prediction = predict(assessment)

`predict` answers a `tcg_grading_companies.port.GradePrediction` — never a
single expected grade, which is CLAUDE.md's central invariant — and in v0.1.0 it
never refuses: ADR 0011 puts the only refusal on the way in, and a thin
assessment widens the distribution instead. The mapping is deterministic and its
spread is **declared, not fitted**, because there is nothing to fit it to.

A workspace member of its own because spec §2.2 says so: PSA, TAG and BGS get
three models, and a shared one would be the universal ``condition_score →
grade`` mapping the architecture forbids. It depends on
`packages/grading-companies` for `GradePrediction` and PSA's ladder and never
the reverse (ADR 0011 decision 5), and it binds no OpenCV — it reads an
assessment somebody else produced and never opens an image.
"""

from tcg_ml_grading_psa.predictor import predict
from tcg_ml_grading_psa.thresholds import (
    DEFAULT_PSA_GRADING_THRESHOLDS,
    GRADING_PSA_VERSION,
    PSAGradingThresholds,
)

__all__ = [
    "DEFAULT_PSA_GRADING_THRESHOLDS",
    "GRADING_PSA_VERSION",
    "PSAGradingThresholds",
    "predict",
]
