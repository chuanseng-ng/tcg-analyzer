"""Corner classification — spec §14, issue #183.

The second of M7's four axis analyzers: the normalized artifact in, one
side's four per-corner findings out. Usage::

    from tcg_ml_corners import classify

    corners = classify(artifact_bytes, card_frame=frame)

`classify` answers for one side with exactly the mapping
`tcg_domain.condition.ConditionAssessment.corners` takes per side. v0.1.0
claims only `clean`, `whitening` and `unknown` — the classical baseline
never guesses a label it cannot see, and a corner it cannot judge is
`unknown` with a confidence, never a guessed `clean`.

A workspace member of its own because it binds to OpenCV: the API image must
not acquire the CV stack, and this package joins the worker extra when
something in the worker first imports it.
"""

from tcg_ml_corners.classifier import classify
from tcg_ml_corners.thresholds import (
    CORNERS_VERSION,
    DEFAULT_CORNER_THRESHOLDS,
    CornerThresholds,
)

__all__ = [
    "CORNERS_VERSION",
    "DEFAULT_CORNER_THRESHOLDS",
    "CornerThresholds",
    "classify",
]
