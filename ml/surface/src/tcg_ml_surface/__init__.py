"""Surface classification: spec §16's defects, judged from the artifact.

The fourth axis analyzer (#185): artifact bytes in, one side's
`SurfaceAssessment` out. v0.1.0 claims `stain` and `scuff` only; the fine
four are refused class-level per ADR 0010 and the other five coarse classes
each carry their own reason in `not_assessed`. Composition into
`ConditionAssessment` is #186's.
"""

from tcg_ml_surface.classifier import classify
from tcg_ml_surface.thresholds import (
    DEFAULT_SURFACE_THRESHOLDS,
    SURFACE_VERSION,
    SurfaceThresholds,
)

__all__ = [
    "DEFAULT_SURFACE_THRESHOLDS",
    "SURFACE_VERSION",
    "SurfaceThresholds",
    "classify",
]
