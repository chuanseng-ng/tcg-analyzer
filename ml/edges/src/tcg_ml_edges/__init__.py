"""Edge classification from the normalized artifact — spec §15, issue #184.

The third axis analyzer. `classify` takes one side's artifact bytes and the
card frame the caller derives from the stored normalization record, and
answers the domain's own per-side shape —
``Uncertain[Mapping[EdgeRegion, RegionFinding]]`` — or refuses the side.
Composition into a `ConditionAssessment` is #186's, not this package's.
"""

from tcg_ml_edges.classifier import classify
from tcg_ml_edges.thresholds import (
    DEFAULT_EDGE_THRESHOLDS,
    EDGES_VERSION,
    EdgeThresholds,
)

__all__ = [
    "DEFAULT_EDGE_THRESHOLDS",
    "EDGES_VERSION",
    "EdgeThresholds",
    "classify",
]
