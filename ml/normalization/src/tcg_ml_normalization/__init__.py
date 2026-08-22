"""Perspective correction and normalization — spec §18, issue #38.

Spec §18 puts this stage after card detection and before card identification,
and M2's acceptance criterion is the artifact it produces: "uploaded card images
produce standardized image artifacts". This package is that stage — an OpenCV
warp, no model — and it is the common input every M7 and M8 model reads, so a
change to its resolution or its resampling is a change to what those models were
trained against.

It is a workspace member of its own, and separate from `services/api`, for the
reason `ml/image-quality` and `ml/card-detection` are: the API image must not
acquire the CV stack. `tcg_api.analysis.jobs` imports the wiring lazily,
`infrastructure/docker/worker.Dockerfile` is the only image that installs this,
and `services/api/tests/test_import_purity.py` keeps that true. It depends on
`tcg-domain` and **not** on `ml/card-detection`: the quadrilateral crosses
between them as a domain type, which is what keeps two siblings from being
coupled.

    from tcg_ml_normalization import normalize

    artifact = normalize(image_bytes, geometry)
    artifact.data            # a 756 x 1056 PNG, exactly 63:88
    artifact.matrix          # original -> artifact, 3x3 row-major
    artifact.quarter_turns   # how far the traversal was rotated

`normalize` returns
:data:`~tcg_domain.confidence.InsufficientInformation` rather than guessing when
the bytes do not decode, and it never enhances the photograph — see
:mod:`tcg_ml_normalization.normalizer` for why that is the whole point.
"""

from __future__ import annotations

from tcg_ml_normalization.normalizer import Normalized, normalize
from tcg_ml_normalization.thresholds import (
    DEFAULT_NORMALIZATION_THRESHOLDS,
    MEDIA_TYPE,
    NORMALIZATION_VERSION,
    NormalizationThresholds,
)

__all__ = [
    "DEFAULT_NORMALIZATION_THRESHOLDS",
    "MEDIA_TYPE",
    "NORMALIZATION_VERSION",
    "NormalizationThresholds",
    "Normalized",
    "normalize",
]
