"""BGS grade prediction — spec §24, issue #225.

The last of M8's three per-company models: M7's neutral condition representation
in, a probability distribution over BGS's own **nineteen**-grade ladder out.
Usage::

    from tcg_ml_grading_bgs import predict

    prediction = predict(assessment)

`predict` answers a `tcg_grading_companies.port.GradePrediction` — never a single
expected grade, never one of BGS's four printed subgrades, and never Black Label
— and in v0.1.0 it never refuses: ADR 0011 puts the only refusal on the way in,
and a thin assessment widens the distribution instead. The mapping is
deterministic and its spread is **declared, not fitted**, because there is
nothing to fit it to.

This is the package where spec §24's own closing sentence — *"BGS must support
half grades"* — becomes executable. BGS issues nineteen grades where PSA and TAG
issue eighteen, and the extra one is **9.5**: the grade the three companies
actually disagree about, and the reason `GET /cards/{id}/market` returns 55
`(company, grade)` pairs rather than 54.

A workspace member of its own because spec §2.2 says so. Its rule is the third
and last shape: PSA walks a centre down the ladder by a weighted sum of damage,
TAG scores four categories out of 1000 and places the score in a band table of
unequal widths, and BGS takes the **worst of four printed subgrades**, quantised
to half grades, as the exact distribution of their minimum. `predictor.py`'s
docstring states the shape and `tests/test_grade_predictors_differ.py` asserts
the consequences against both siblings.

It depends on `packages/grading-companies` for `GradePrediction` and BGS's ladder
and never the reverse (ADR 0011 decision 5); it **imports neither sibling
predictor**, for anything; and it binds no OpenCV — it reads an assessment
somebody else produced and never opens an image.
"""

from tcg_ml_grading_bgs.predictor import predict
from tcg_ml_grading_bgs.thresholds import (
    DEFAULT_BGS_GRADING_THRESHOLDS,
    GRADING_BGS_VERSION,
    BGSGradingThresholds,
)

__all__ = [
    "DEFAULT_BGS_GRADING_THRESHOLDS",
    "GRADING_BGS_VERSION",
    "BGSGradingThresholds",
    "predict",
]
