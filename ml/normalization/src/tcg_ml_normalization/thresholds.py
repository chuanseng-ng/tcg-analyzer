"""The numbers normalization runs on — issue #38.

`ml/card-detection/thresholds.py` is the model, and the reasoning transfers
whole: a frozen dataclass a caller may replace wholesale rather than
`TCG_API_*` variables no deployment tunes independently, and
:meth:`NormalizationThresholds.as_record` so that a stored artifact explains
itself without anybody knowing what was configured at the time. The keys are
prefixed for the same reason the detector's are — three dataclasses now write
into records that get merged, and prefixing at the source means they cannot
collide however a merge is written.

**Changing a value means bumping the version.** Every artifact this package
writes is an input to the M7 and M8 models, so a resolution or a resampling
change is a change to what those models were trained against;
:data:`NORMALIZATION_VERSION` names a fixed set of numbers the way a model
bundle names fixed weights.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = [
    "CARD_HEIGHT_MM",
    "CARD_WIDTH_MM",
    "DEFAULT_NORMALIZATION_THRESHOLDS",
    "MEDIA_TYPE",
    "NORMALIZATION_VERSION",
    "NormalizationThresholds",
]

#: What produced an artifact. Recorded on every image normalization ran
#: against; never a pointer to "current", per the project's versioning
#: invariant.
NORMALIZATION_VERSION: Final = "normalization-opencv-v0.1.0"

#: A trading card, in millimetres. A physical fact rather than a threshold,
#: which is why it is not tunable and why `ml/card-detection` deriving
#: `CARD_ASPECT` from the same two numbers is not a duplicated setting.
CARD_WIDTH_MM: Final = 63.0
CARD_HEIGHT_MM: Final = 88.0

#: The artifact is a PNG, and the caller stores it under this type. Lossless
#: deliberately: JPEG's 8x8 blocking fabricates precisely the fine surface
#: texture the stages downstream exist to measure.
MEDIA_TYPE: Final = "image/png"


@dataclass(frozen=True, slots=True)
class NormalizationThresholds:
    """The output's size, and how much intermediate resolution the warp keeps.

    Raises:
        ValueError: If a value is not positive, or if the resolution does not
            put a whole number of pixels on both of the card's edges.
    """

    #: The output resolution, as pixels per millimetre of card. 12 gives
    #: 756 x 1056, which is *exactly* 63:88 — so the artifact has a real card's
    #: proportions with no rounding, and a centering ratio measured on it means
    #: what it says. Roughly 305 dpi.
    pixels_per_mm: float = 12.0

    #: The warp's intermediate is this multiple of the output at most. The warp
    #: is done at approximately the card's size in the original and then box
    #: filtered down, because warping a 4000-pixel card straight to 1056
    #: aliases — and aliasing is fabricated surface texture, which is the one
    #: thing this stage must not produce. Above about 4x the box filter has
    #: nothing left to gain, and the intermediate is where the memory goes.
    max_warp_multiple: int = 4

    #: zlib's level for the encoded artifact. Pinned rather than left to
    #: OpenCV's default so that the same photograph encodes to the same bytes
    #: whatever OpenCV decides its default should be.
    png_compression: int = 6

    def __post_init__(self) -> None:
        if self.pixels_per_mm <= 0.0:
            raise ValueError(f"pixels_per_mm must be positive, got {self.pixels_per_mm!r}")
        if self.max_warp_multiple < 1:
            raise ValueError(
                f"the intermediate cannot be smaller than the output, so "
                f"max_warp_multiple is at least 1, got {self.max_warp_multiple!r}"
            )
        if not 0 <= self.png_compression <= 9:
            raise ValueError(f"png_compression must lie in [0, 9], got {self.png_compression!r}")

        # The 63:88 claim is the reason this resolution was chosen, so it is
        # checked rather than trusted: a pixels_per_mm that lands mid-pixel on
        # either edge distorts the aspect by however much it rounds.
        for label, millimetres in (("width", CARD_WIDTH_MM), ("height", CARD_HEIGHT_MM)):
            exact = millimetres * self.pixels_per_mm
            if abs(exact - round(exact)) > 1e-9:
                raise ValueError(
                    f"pixels_per_mm must put a whole number of pixels on the card's "
                    f"{label}, but {self.pixels_per_mm!r} gives {exact!r}"
                )

    @property
    def target_width(self) -> int:
        """The artifact's width in pixels."""
        return round(CARD_WIDTH_MM * self.pixels_per_mm)

    @property
    def target_height(self) -> int:
        """The artifact's height in pixels."""
        return round(CARD_HEIGHT_MM * self.pixels_per_mm)

    def as_record(self) -> dict[str, float]:
        """The form persisted alongside the artifact.

        Prefixed, so it cannot collide with the gate's or the detector's own
        record — see the module docstring.
        """
        return {f"normalization_{name}": float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`NORMALIZATION_VERSION` names them.
DEFAULT_NORMALIZATION_THRESHOLDS: Final = NormalizationThresholds()
