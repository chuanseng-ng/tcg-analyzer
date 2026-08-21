"""The image-quality gate — spec §19, issue #36.

Spec §18 puts this stage between file validation and card detection, and spec
§19 fixes what it may conclude: `unusable` stops the analysis, `poor` continues
but the user must be told. This package is the M2 implementation — OpenCV
heuristics, no model — and M7 replaces its internals behind the same
:func:`assess` signature and the same
:class:`~tcg_domain.image_quality.QualityReport`.

It is a workspace member of its own, and separate from `services/api`, because
the API image must not acquire the CV stack: `tcg_api.analysis.jobs` imports the
wiring lazily and `infrastructure/docker/worker.Dockerfile` is the only image
that installs this. `services/api/tests/test_import_purity.py` keeps that true.

    from tcg_ml_image_quality import assess

    report = assess(image_bytes)
    report.status        # spec §19's verdict
    report.score         # [0, 1], 1 being best
    report.as_record()   # what `images.quality_details` holds
"""

from __future__ import annotations

from tcg_ml_image_quality.gate import UnreadableImage, assess
from tcg_ml_image_quality.thresholds import (
    DEFAULT_THRESHOLDS,
    IMAGE_QUALITY_VERSION,
    QualityThresholds,
)

__all__ = [
    "DEFAULT_THRESHOLDS",
    "IMAGE_QUALITY_VERSION",
    "QualityThresholds",
    "UnreadableImage",
    "assess",
]
