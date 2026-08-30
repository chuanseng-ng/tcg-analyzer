"""Corner classification — spec §14, issue #183.

Artifact bytes in, one side's four per-corner findings out — or
:class:`~tcg_domain.confidence.InsufficientInformation` when the side cannot
be judged at all. `ml/centering` is the model for the shape of this module:
no database, no object storage, no HTTP, and the card rectangle is the frame
the caller names from the artifact's *stored* normalization record, never
found here and never assumed to be the image boundary.

**v0.1.0 answers `clean`, `whitening` or `unknown` — nothing else.** Rounding
needs a sub-pixel arc model against an unknown per-print nominal radius, and
dents, creases and layering are depth and gloss signals a single normalized
view does not carry; at 84 px none survives an honest heuristic, so none is
claimed. The five unclaimed labels stay reachable only through a learned
model, which enters through `ml/evaluation`'s benchmark (#188) behind this
same interface — epic #8's decision 1. #188 also measures what the
restriction costs against the annotated corpus.

**Whitening is exposed paper core**: achromatic, bright pixels inside a
1 mm band along the card's edges at the corner, where every printed border
except a white one is saturated or dark. The same band one step deeper
samples the printed border itself — a card whose border *is* near-white
makes whitening indistinguishable in this signal, and that corner answers
`unknown` rather than guessing `clean` (§2.7's confidently-wrong output,
refused). Inside the crop's tip square the card ends at its cut-corner arc,
and everything beyond the arc is photographed background — near-white on a
white surface, and never damage.

**`clean` is a positive claim** (§14 lists it; the schema's absence rule is
the annotator's, not an analyzer's): it is made only after the border proved
not-white — so whitening would have been visible — and the detection band
showed less core than the despeckled noise floor.

Named ceilings, all #188's to price (ponytail: each names its upgrade path):
the HSV floors are absolute, so an underexposed artifact can hold real
whitening below ``min_white_value`` — the upgrade is thresholding value
relative to the reference band's median; whitening deep enough to flood the
reference band reads as a white border and flips the answer to `unknown`
rather than `severe` — refusal in the ambiguous direction, and damage that
deep also surfaces on the edge axis (#184); a strong specular highlight on
foil can graze `minor` — the despeckle and the noise floor absorb glints,
not highlight bands; and a printing whose corner cut is larger than the
nominal ``corner_radius_px`` arc puts background inside the detection band,
which on a white surface reads as whitening — the upgrade is tracing the
actual arc rather than assuming the nominal one.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray
from tcg_domain.annotation import CornerLabel, CornerRegion, DefectSeverity
from tcg_domain.condition import BoundingBox, RegionFinding
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain

from tcg_ml_corners.thresholds import DEFAULT_CORNER_THRESHOLDS, CornerThresholds

__all__ = ["classify"]

#: The artifact's scale is fixed by construction: 12 px/mm, so areas convert
#: at 144 px²/mm² — the same reason `ml/centering` denominates
#: `min_axis_border_px` in pixels.
_PX_PER_MM2: Final = 144.0

#: Said when the bytes did not decode. This package answers rather than
#: raising, so the one place undecodable bytes become a job failure stays
#: upstream of it.
_UNDECODABLE: Final = "the artifact could not be decoded"

#: Said when the card frame names a region too small to hold two corner
#: crops per axis without overlapping.
_CARD_TOO_SMALL: Final = "the card frame names a region too small to classify"

#: The confidence recipe: margin-from-threshold, scaled into
#: ``[floor, floor + span]``. A heuristic in [0, 1], not a calibrated
#: probability — §26's calibration work is M8's, and #64 gates on the value
#: downstream, which is why it must not flatter itself.
_CONFIDENCE_FLOOR: Final = 0.5
_CONFIDENCE_SPAN: Final = 0.45

#: The refusal's confidence is about "this corner is unjudgeable", which the
#: reference-band fraction establishes but does not grade — flat, on the
#: floor.
_UNKNOWN_CONFIDENCE: Final = 0.5

#: Which axes flip to bring a corner's crop into canonical orientation —
#: card tip at (0, 0), the two card edges along the top and left. One
#: classifier body serves four corners; `CornerRegion`'s reading order.
_FLIPS: Final[Mapping[CornerRegion, tuple[bool, bool]]] = {
    CornerRegion.TOP_LEFT: (False, False),
    CornerRegion.TOP_RIGHT: (False, True),
    CornerRegion.BOTTOM_LEFT: (True, False),
    CornerRegion.BOTTOM_RIGHT: (True, True),
}

_Mask = NDArray[np.bool_]


def classify(
    data: bytes,
    *,
    card_frame: BoundingBox,
    thresholds: CornerThresholds = DEFAULT_CORNER_THRESHOLDS,
) -> Uncertain[Mapping[CornerRegion, RegionFinding]]:
    """Classify one side's four corners from its normalized artifact.

    Args:
        data: The artifact's encoded bytes.
        card_frame: Where the card sits in the artifact, as fractions of the
            unit square — derived from the artifact's stored normalization
            record. `BoundingBox(0, 0, 1, 1)` is a pre-#194 artifact whose
            card really does reach the edges.
        thresholds: What counts as whitening. Replace wholesale or not at all.

    Returns:
        A mapping carrying exactly the four `CornerRegion` keys — the shape
        `ConditionAssessment.corners` takes per side — or
        `InsufficientInformation` naming why the whole side was not judged.
        A corner that cannot be judged individually is the `unknown` label,
        never a missing key.
    """
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        return InsufficientInformation(_UNDECODABLE)

    height, width = decoded.shape[:2]
    left = round(card_frame.x * width)
    top = round(card_frame.y * height)
    crop_width = round(card_frame.width * width)
    crop_height = round(card_frame.height * height)
    if min(crop_width, crop_height) < 2 * thresholds.corner_size_px:
        return InsufficientInformation(_CARD_TOO_SMALL)
    # The rounded rect can overshoot the image by a pixel when both round()s
    # go up; numpy slicing clamps it, and everything downstream reads the
    # slice's actual shape rather than these intended dimensions.
    card = decoded[top : top + crop_height, left : left + crop_width]

    hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
    raw = (hsv[:, :, 1] <= thresholds.max_white_saturation) & (
        hsv[:, :, 2] >= thresholds.min_white_value
    )
    near_white = cv2.morphologyEx(
        raw.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
    ).astype(bool)

    detection, reference = _region_masks(thresholds)
    findings = {
        region: _classify_corner(
            near_white,
            region=region,
            card_origin=(left, top),
            artifact_size=(width, height),
            detection=detection,
            reference=reference,
            thresholds=thresholds,
        )
        for region in CornerRegion
    }
    return MappingProxyType(findings)


def _region_masks(thresholds: CornerThresholds) -> tuple[_Mask, _Mask]:
    """The detection and reference regions, in canonical orientation.

    Both are L-shaped bands following the two card edges — detection at
    depth ``[inset, inset + band)``, reference one band deeper — and both
    are clipped inside the corner-cut arc in the tip square, because beyond
    the arc the picture shows background, not card. The arc clip is
    tightened by the same ``edge_inset_px`` as the straight edges: the
    anti-aliased card/background blend runs along the arc too.
    """
    size = thresholds.corner_size_px
    inset = thresholds.edge_inset_px
    band = thresholds.edge_band_px
    radius = thresholds.corner_radius_px

    rows, columns = np.mgrid[0:size, 0:size]
    depth = np.minimum(rows, columns)
    in_tip = (rows < radius) & (columns < radius)
    inside_arc = (rows - radius) ** 2 + (columns - radius) ** 2 <= (radius - inset) ** 2
    on_card = ~in_tip | inside_arc

    detection = (depth >= inset) & (depth < inset + band) & on_card
    reference = (depth >= inset + band) & (depth < inset + 2 * band) & on_card
    return detection, reference


def _classify_corner(
    near_white: _Mask,
    *,
    region: CornerRegion,
    card_origin: tuple[int, int],
    artifact_size: tuple[int, int],
    detection: _Mask,
    reference: _Mask,
    thresholds: CornerThresholds,
) -> RegionFinding:
    size = thresholds.corner_size_px
    card_height, card_width = near_white.shape
    flip_rows, flip_columns = _FLIPS[region]
    row0 = card_height - size if flip_rows else 0
    column0 = card_width - size if flip_columns else 0
    crop = near_white[row0 : row0 + size, column0 : column0 + size]
    if flip_rows:
        crop = np.flipud(crop)
    if flip_columns:
        crop = np.fliplr(crop)

    if (crop & reference).sum() / reference.sum() >= thresholds.white_border_fraction:
        return RegionFinding(
            label=CornerLabel.UNKNOWN,
            confidence=Confidence.of(_UNKNOWN_CONFIDENCE),
        )

    core = crop & detection
    area_mm2 = float(core.sum()) / _PX_PER_MM2
    clean_max = thresholds.clean_max_area_mm2
    if area_mm2 <= clean_max:
        margin = min(1.0, (clean_max - area_mm2) / clean_max)
        return RegionFinding(
            label=CornerLabel.CLEAN,
            confidence=Confidence.of(_CONFIDENCE_FLOOR + _CONFIDENCE_SPAN * margin),
        )

    if area_mm2 < thresholds.moderate_min_area_mm2:
        severity = DefectSeverity.MINOR
    elif area_mm2 < thresholds.severe_min_area_mm2:
        severity = DefectSeverity.MODERATE
    else:
        severity = DefectSeverity.SEVERE
    margin = min(1.0, (area_mm2 - clean_max) / clean_max)
    return RegionFinding(
        label=CornerLabel.WHITENING,
        confidence=Confidence.of(_CONFIDENCE_FLOOR + _CONFIDENCE_SPAN * margin),
        severity=severity,
        bounding_box=_core_box(
            core,
            size=size,
            flips=(flip_rows, flip_columns),
            crop_origin=(row0, column0),
            card_origin=card_origin,
            artifact_size=artifact_size,
        ),
    )


def _core_box(
    core: _Mask,
    *,
    size: int,
    flips: tuple[bool, bool],
    crop_origin: tuple[int, int],
    card_origin: tuple[int, int],
    artifact_size: tuple[int, int],
) -> BoundingBox:
    """The tight box around the detected core, as artifact fractions (§17).

    `core` is non-empty by construction — the caller only asks past the
    clean threshold — and the canonical flips are undone before the crop's
    origin and the card's origin place the box in the artifact.
    """
    rows, columns = np.nonzero(core)
    row_first, row_last = int(rows.min()), int(rows.max())
    column_first, column_last = int(columns.min()), int(columns.max())
    flip_rows, flip_columns = flips
    if flip_rows:
        row_first, row_last = size - 1 - row_last, size - 1 - row_first
    if flip_columns:
        column_first, column_last = size - 1 - column_last, size - 1 - column_first

    left, top = card_origin
    width, height = artifact_size
    x = (left + crop_origin[1] + column_first) / width
    y = (top + crop_origin[0] + row_first) / height
    return BoundingBox(
        x=x,
        y=y,
        width=min((column_last - column_first + 1) / width, 1.0 - x),
        height=min((row_last - row_first + 1) / height, 1.0 - y),
    )
