"""Template-aware centering measurement — spec §21, issue #182.

The first of M7's four axis analyzers: the normalized artifact in, spec §13's
four centering ratios and a confidence out. Usage::

    from tcg_ml_centering import measure

    side = measure(artifact_bytes, card_frame=frame)

`measure` answers for one side; `centering_of` composes a front and a back
into :class:`tcg_domain.condition.Centering`. A template with no conventional
border — full-art, borderless, anything unrecognised — is
`insufficient_information`, never a ratio measured against a frame that is
not there (§21's own requirement).

A workspace member of its own because it binds to OpenCV: the API image must
not acquire the CV stack, and this package joins the worker extra when
something in the worker first imports it.
"""

from tcg_ml_centering.measurer import SideCentering, centering_of, measure
from tcg_ml_centering.thresholds import (
    CENTERING_VERSION,
    DEFAULT_CENTERING_THRESHOLDS,
    CenteringThresholds,
)

__all__ = [
    "CENTERING_VERSION",
    "DEFAULT_CENTERING_THRESHOLDS",
    "CenteringThresholds",
    "SideCentering",
    "centering_of",
    "measure",
]
