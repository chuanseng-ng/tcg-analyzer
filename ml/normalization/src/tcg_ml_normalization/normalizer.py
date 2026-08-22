"""Perspective correction and normalization — spec §18, issue #38.

Bytes and a quadrilateral in, the standardized artifact out. `ml/card-detection`
is the model for the shape of this module and for the same reason: no database,
no object storage, no HTTP, so everything worth asserting about it can be
asserted by a test that needs no infrastructure.

**Nothing here enhances the photograph.** No sharpening, no denoising, no
contrast stretching, no white balance. Every stage downstream exists to measure
scratches, whitening and print lines; a denoised scratch is a scratch the model
cannot see and a sharpened edge is whitening that was never there. The only
signal processing is the resampling a warp cannot avoid.

**That resampling is done in two steps, and it is not fussiness.**
`cv2.warpPerspective` offers no area filter, so warping a 4000-pixel card
straight down to 1056 point-samples it — and the moire that comes back is
fabricated surface texture, exactly what the paragraph above forbids. So the
warp goes to an integer multiple of the output instead, at roughly the card's
size in the original, and one box filter takes it down. `INTER_LINEAR` for the
warp rather than `INTER_CUBIC`: cubic overshoots at a high-contrast edge, and
an overshoot at a card's border reads as whitening.

**Colour-space normalization is a defined output space, not a colour
transform.** `IMREAD_COLOR` gives 8-bit, 3-channel BGR whatever arrived — a
16-bit PNG, a grayscale scan, a CMYK JPEG — and the artifact is written as a
lossless PNG of exactly that. The embedded ICC profile is *not* honoured, so an
iPhone's Display P3 numbers are read as sRGB.

    ponytail: no ICC transform. Every V1 consumer of this artifact measures
    geometry or luminance, so a colour cast costs nothing yet. Read the profile
    with Pillow's `ImageCms` and convert to sRGB when a consumer needs colour to
    mean something — the profile is still on the original, because the upload
    endpoint keeps APP2 on purpose.

**Failure is a result, not an exception.** Undecodable bytes answer
:class:`~tcg_domain.confidence.InsufficientInformation`, as the detector's do,
so that the one place bad bytes become a job failure stays
`tcg_ml_image_quality.UnreadableImage`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import cv2
import numpy as np
from cv2.typing import MatLike
from tcg_domain.card_geometry import CardGeometry, Corner
from tcg_domain.confidence import InsufficientInformation, Uncertain

from tcg_ml_normalization.thresholds import (
    DEFAULT_NORMALIZATION_THRESHOLDS,
    MEDIA_TYPE,
    NORMALIZATION_VERSION,
    NormalizationThresholds,
)

__all__ = ["Normalized", "normalize"]

_Quad = tuple[Corner, Corner, Corner, Corner]

#: Said when the bytes did not decode. The gate raises for this case; this
#: package answers, for the reason `ml/card-detection` gives.
_UNDECODABLE: Final = "the photograph could not be decoded"

#: Said when the encoder refused the warped card, which should not happen and is
#: reported rather than asserted away.
_UNENCODABLE: Final = "the normalized card could not be encoded"


@dataclass(frozen=True, slots=True)
class Normalized:
    """One standardized card artifact, and how it was produced.

    Args:
        data: The artifact itself, a PNG of :data:`MEDIA_TYPE`.
        width: Its width in pixels.
        height: Its height in pixels.
        matrix: The 3x3 projective transform taking a point in the **original**
            photograph to its place in this artifact, row-major. Persisted
            because spec §51's post-V1 defect visualisation has to draw boxes on
            the original, and without this that mapping is unrecoverable.
        quarter_turns: How far the corner traversal was rotated to put the
            card's short edge first — 0 or 1. It is *not* a claim about which
            way up the card is printed: the detector anchors its traversal at
            the corner nearest the frame origin, so a card photographed on its
            side comes back correctly proportioned but rotated, and only reading
            the artwork could say by how much. That is card identification's
            question, not this stage's.
        version: What produced this.
        thresholds: The numbers it ran with.
    """

    data: bytes
    width: int
    height: int
    matrix: tuple[float, ...]
    quarter_turns: int
    version: str = NORMALIZATION_VERSION
    thresholds: Mapping[str, float] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "thresholds", MappingProxyType(dict(self.thresholds)))

    @property
    def media_type(self) -> str:
        """What the caller stores the artifact under."""
        return MEDIA_TYPE

    def as_record(self) -> dict[str, object]:
        """The form persisted to `images.normalization_details`.

        Plain JSON-compatible types, so the caller writes it to a JSONB column
        without a serializer knowing anything about this package. The artifact's
        own bytes are deliberately absent — they are the object, not the record.
        """
        return {
            "version": self.version,
            "width": self.width,
            "height": self.height,
            "quarter_turns": self.quarter_turns,
            "matrix": list(self.matrix),
            "thresholds": dict(self.thresholds),
        }

    def __str__(self) -> str:
        return f"{self.width}x{self.height} card ({self.version})"


def normalize(
    data: bytes,
    geometry: CardGeometry,
    *,
    thresholds: NormalizationThresholds = DEFAULT_NORMALIZATION_THRESHOLDS,
) -> Uncertain[Normalized]:
    """Straighten one photographed card into the standardized artifact.

    Args:
        data: The stored image, JPEG or PNG. The original is not modified — this
            function reads bytes and returns new ones.
        geometry: Where the card is, from `ml/card-detection`. Its corners are
            read positionally and its side lengths are read off the dataclass
            rather than recomputed, so that this stage and the quality gate
            cannot disagree about what the same quadrilateral means.
        thresholds: The output resolution and how much intermediate the warp
            keeps. Recorded by the caller alongside the artifact.

    Returns:
        A :class:`Normalized`, or
        :class:`~tcg_domain.confidence.InsufficientInformation` when the bytes
        do not decode or the artifact does not encode. Never raises for either.
    """
    colour = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if colour is None:
        return InsufficientInformation(_UNDECODABLE)

    corners, quarter_turns = _short_edge_first(geometry)
    multiple = _warp_multiple(corners, thresholds)
    target = (thresholds.target_width, thresholds.target_height)
    warp = (target[0] * multiple, target[1] * multiple)

    # The detected quadrilateral is warped *whole*: no inset, because the
    # detector returns the outermost member of its group on purpose and M7's
    # edge and corner analysis needs the card's real edge, which a tight crop
    # shaves off.
    warp_matrix = cv2.getPerspectiveTransform(
        np.array(corners, dtype=np.float32),
        np.array(
            [
                (0.0, 0.0),
                (warp[0] - 1.0, 0.0),
                (warp[0] - 1.0, warp[1] - 1.0),
                (0.0, warp[1] - 1.0),
            ],
            dtype=np.float32,
        ),
    )
    warped: MatLike = cv2.warpPerspective(colour, warp_matrix, warp, flags=cv2.INTER_LINEAR)

    # An exact integer factor, so this is a plain box average rather than a
    # resampling with weights that vary across the image.
    card: MatLike = (
        warped if multiple == 1 else cv2.resize(warped, target, interpolation=cv2.INTER_AREA)
    )

    encoded, buffer = cv2.imencode(
        ".png", card, [cv2.IMWRITE_PNG_COMPRESSION, thresholds.png_compression]
    )
    if not encoded:
        return InsufficientInformation(_UNENCODABLE)

    return Normalized(
        data=bytes(buffer.tobytes()),
        width=target[0],
        height=target[1],
        matrix=_composed(warp_matrix, shrink=1.0 / multiple),
        quarter_turns=quarter_turns,
        version=NORMALIZATION_VERSION,
        thresholds=thresholds.as_record(),
    )


def _short_edge_first(geometry: CardGeometry) -> tuple[_Quad, int]:
    """The corners rotated so the traversal's first edge is the card's short one.

    The detector anchors its clockwise traversal at the corner nearest the frame
    origin, which for a card photographed on its side makes ``corners[0] ->
    corners[1]`` the card's *long* edge. Warping that onto a portrait target
    would squash the card and every centering ratio measured on it would be
    wrong. Rotating the tuple by one position fixes it and preserves the
    clockwise order, because a cyclic rotation cannot reverse a cycle.

    What this does not do is decide which way up the card is printed. That needs
    the artwork read, which is card identification's job.
    """
    top, right, bottom, left = geometry.side_lengths
    if top + bottom <= left + right:
        return geometry.corners, 0
    rotated = geometry.corners[1:] + geometry.corners[:1]
    return (rotated[0], rotated[1], rotated[2], rotated[3]), 1


def _warp_multiple(corners: _Quad, thresholds: NormalizationThresholds) -> int:
    """How many times the output resolution the warp's intermediate should be.

    Chosen so the warp samples the original at roughly one output pixel per
    source pixel — enough that the warp itself does not have to decimate, and no
    more than that, since the box filter afterwards is what removes the detail
    the output cannot hold.
    """
    long_edge = max(
        _distance(corners[1], corners[2]),
        _distance(corners[3], corners[0]),
    )
    wanted = round(long_edge / thresholds.target_height)
    return max(1, min(thresholds.max_warp_multiple, wanted))


def _composed(warp_matrix: MatLike, *, shrink: float) -> tuple[float, ...]:
    """The whole original-to-artifact transform, warp and box filter together.

    `cv2.resize` maps the continuous coordinate ``x`` to ``x * shrink`` where
    `warpPerspective` maps pixel *indices*, so the two conventions differ by up
    to a pixel at the far edge. That is far below the precision of the corners
    themselves, which the detector measures on a 1024-pixel working copy and
    scales back up, and it is not worth a correction that would only look
    precise.
    """
    scaled = np.diag(np.array([shrink, shrink, 1.0])) @ warp_matrix
    return tuple(float(value) for value in scaled.ravel())


def _distance(start: Corner, end: Corner) -> float:
    return float(np.hypot(end[0] - start[0], end[1] - start[1]))
