"""TAG grade prediction — spec §24, issue #224.

The second of M8's three per-company models: M7's neutral condition
representation in, a probability distribution over TAG's own eighteen-grade
ladder out. Usage::

    from tcg_ml_grading_tag import predict

    prediction = predict(assessment)

`predict` answers a `tcg_grading_companies.port.GradePrediction` — never a single
expected grade, and never one of TAG's two grade-10 designations — and in v0.1.0
it never refuses: ADR 0011 puts the only refusal on the way in, and a thin
assessment widens the distribution instead. The mapping is deterministic and its
spread is **declared, not fitted**, because there is nothing to fit it to.

A workspace member of its own because spec §2.2 says so, and this is the package
where that rule is tested rather than restated: TAG's ladder is index-for-index
identical to PSA's, so a TAG predictor built as PSA's rule with different weights
would be the universal ``condition_score → grade`` mapping the architecture
forbids. It is not. TAG scores four categories on a **1-to-1000 machine scale**
and reaches the ladder through a **band table of unequal widths**, where PSA
walks a centre down in ladder steps; `predictor.py`'s docstring states the shape
and `tests/test_grade_predictors_differ.py` asserts the consequences.

It depends on `packages/grading-companies` for `GradePrediction` and TAG's ladder
and never the reverse (ADR 0011 decision 5); it **imports neither sibling
predictor**, for anything; and it binds no OpenCV — it reads an assessment
somebody else produced and never opens an image.
"""

from tcg_ml_grading_tag.predictor import predict
from tcg_ml_grading_tag.thresholds import (
    DEFAULT_TAG_GRADING_THRESHOLDS,
    GRADING_TAG_VERSION,
    TAG_SCORE_MAXIMUM,
    TAGGradingThresholds,
)

__all__ = [
    "DEFAULT_TAG_GRADING_THRESHOLDS",
    "GRADING_TAG_VERSION",
    "TAG_SCORE_MAXIMUM",
    "TAGGradingThresholds",
    "predict",
]
