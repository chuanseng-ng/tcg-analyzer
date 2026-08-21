"""Card boundary detection — spec §18, issue #37.

Spec §18 puts this stage between the image-quality gate and perspective
correction, and #36 left the gate five of spec §19's eleven conditions it could
not answer without it. This package is the M2 implementation — an OpenCV contour
baseline, no model — and a learned detector is an M7 option that must not change
:func:`detect`'s signature or
:class:`~tcg_domain.card_geometry.CardGeometry`.

It is a workspace member of its own, and separate from `services/api`, for the
reason `ml/image-quality` is: the API image must not acquire the CV stack.
`tcg_api.analysis.jobs` imports the wiring lazily,
`infrastructure/docker/worker.Dockerfile` is the only image that installs this,
and `services/api/tests/test_import_purity.py` keeps that true.

    from tcg_ml_card_detection import detect

    geometry = detect(image_bytes)
    geometry.corners            # four (x, y), clockwise from the top left
    geometry.confidence         # how card-like the quadrilateral is
    geometry.area_fraction      # how much of the frame the card fills

`detect` returns :data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION` rather
than guessing when there is no card to find, which is what lets the gate degrade
honestly instead of judging a quadrilateral nobody found.
"""

from __future__ import annotations

from tcg_ml_card_detection.detector import detect
from tcg_ml_card_detection.thresholds import (
    CARD_ASPECT,
    CARD_DETECTION_VERSION,
    DEFAULT_DETECTION_THRESHOLDS,
    DetectionThresholds,
)

__all__ = [
    "CARD_ASPECT",
    "CARD_DETECTION_VERSION",
    "DEFAULT_DETECTION_THRESHOLDS",
    "DetectionThresholds",
    "detect",
]
