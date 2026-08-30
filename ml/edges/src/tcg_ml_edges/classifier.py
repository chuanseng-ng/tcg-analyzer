"""Edge classification — spec §15, issue #184.

Artifact bytes in, one side's four per-edge findings out — or
:class:`~tcg_domain.confidence.InsufficientInformation` when the side cannot
be judged at all. `ml/corners` is the model for the shape of this module:
no database, no object storage, no HTTP, and the card rectangle is the frame
the caller names from the artifact's *stored* normalization record, never
found here and never assumed to be the image boundary.

**v0.1.0 answers `clean`, `whitening` or `unknown` — nothing else.** Chips
and notches need shape analysis against the cut line, `rough_cut` is a
whole-edge texture claim, and layering and dents are depth signals a single
normalized view does not carry; none survives an honest heuristic at
12 px/mm, so none is claimed. The five unclaimed labels stay reachable only
through a learned model, which enters through `ml/evaluation`'s benchmark
(#188) behind this same interface — epic #8's decision 1. #188 also measures
what the restriction costs against the annotated corpus.

**The corner/edge boundary (#184's deliverable):** an edge's run is the card
edge minus the first and last ``corner_exclusion_px`` (84 px, the 7 mm
corner crop `ml/corners` judges) — a defect inside that square is the corner
result's, and reporting it here too would double-report one defect across
two axes. The evaluation needs the line to score either axis, and this
number is its definition.

**Whitening is exposed paper core**: achromatic, bright pixels inside a
1 mm band along the card's edge, where every printed border except a white
one is saturated or dark. The same band one step deeper samples the printed
border itself — a card whose border *is* near-white makes whitening
indistinguishable in this signal, and that edge answers `unknown` rather
than guessing `clean` (§2.7's confidently-wrong output, refused). **`clean`
is a positive claim** (§15 lists it; the schema's absence rule is the
annotator's, not an analyzer's): it is made only after the border proved
not-white and the detection band showed less core than the despeckled noise
floor. The detector's largest-in-group rule keeps edge whitening inside the
artifact on purpose (#37) — so if every edge band reads clean on a card
known to be worn, suspect the crop before believing the card.

Named ceilings, all #188's to price (ponytail: each names its upgrade path):
the HSV floors are absolute, so an underexposed artifact can hold real
whitening below ``min_white_value`` — the upgrade is thresholding value
relative to the reference band's median; whitening deep enough to flood the
reference band reads as a white border and flips the answer to `unknown`
rather than `severe` — refusal in the ambiguous direction, the corner axis's
same trade; a strong specular highlight on foil can graze `minor` — the
despeckle and the noise floor absorb glints, not highlight bands; and the
severity boundaries treat one long shallow scuff and one short deep chip of
equal area alike — banded area carries no shape, and shape is the learned
model's to earn.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray
from tcg_domain.annotation import DefectSeverity, EdgeLabel, EdgeRegion
from tcg_domain.condition import BoundingBox, RegionFinding
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain

from tcg_ml_edges.thresholds import DEFAULT_EDGE_THRESHOLDS, EdgeThresholds

__all__ = ["classify"]

#: The artifact's scale is fixed by construction: 12 px/mm, so areas convert
#: at 144 px²/mm² — the same reason `ml/centering` denominates
#: `min_axis_border_px` in pixels.
_PX_PER_MM2: Final = 144.0

#: Said when the bytes did not decode. This package answers rather than
#: raising, so the one place undecodable bytes become a job failure stays
#: upstream of it.
_UNDECODABLE: Final = "the artifact could not be decoded"

#: Said when the card frame names a region too small to hold an edge run
#: between its two corner exclusions, or opposite edges' bands would
#: overlap.
_CARD_TOO_SMALL: Final = "the card frame names a region too small to classify"

#: The confidence recipe: margin-from-threshold, scaled into
#: ``[floor, floor + span]``. A heuristic in [0, 1], not a calibrated
#: probability — §26's calibration work is M8's, and #64 gates on the value
#: downstream, which is why it must not flatter itself.
_CONFIDENCE_FLOOR: Final = 0.5
_CONFIDENCE_SPAN: Final = 0.45

#: The refusal's confidence is about "this edge is unjudgeable", which the
#: reference-band fraction establishes but does not grade — flat, on the
#: floor.
_UNKNOWN_CONFIDENCE: Final = 0.5

_Mask = NDArray[np.bool_]


def classify(
    data: bytes,
    *,
    card_frame: BoundingBox,
    thresholds: EdgeThresholds = DEFAULT_EDGE_THRESHOLDS,
) -> Uncertain[Mapping[EdgeRegion, RegionFinding]]:
    """Classify one side's four edges from its normalized artifact.

    Args:
        data: The artifact's encoded bytes.
        card_frame: Where the card sits in the artifact, as fractions of the
            unit square — derived from the artifact's stored normalization
            record. `BoundingBox(0, 0, 1, 1)` is a pre-#194 artifact whose
            card really does reach the edges.
        thresholds: What counts as whitening. Replace wholesale or not at all.

    Returns:
        A mapping carrying exactly the four `EdgeRegion` keys — the shape
        `ConditionAssessment.edges` takes per side — or
        `InsufficientInformation` naming why the whole side was not judged.
        An edge that cannot be judged individually is the `unknown` label,
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
    card = decoded[top : top + crop_height, left : left + crop_width]
    # The gate reads the slice's actual shape, not the intended dimensions:
    # a run must fit between the two corner exclusions in both axes, and
    # opposite edges' bands must not overlap. An edge run one pixel short of
    # this would leave the reference band empty and its fraction undefined.
    minimum = max(
        2 * thresholds.corner_exclusion_px + 1,
        2 * (thresholds.edge_inset_px + 2 * thresholds.edge_band_px),
    )
    if min(card.shape[:2]) < minimum:
        return InsufficientInformation(_CARD_TOO_SMALL)

    hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
    raw = (hsv[:, :, 1] <= thresholds.max_white_saturation) & (
        hsv[:, :, 2] >= thresholds.min_white_value
    )
    near_white = cv2.morphologyEx(
        raw.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
    ).astype(bool)

    findings = {
        region: _classify_edge(
            near_white,
            region=region,
            card_origin=(left, top),
            artifact_size=(width, height),
            thresholds=thresholds,
        )
        for region in EdgeRegion
    }
    return MappingProxyType(findings)


def _band_masks(
    shape: tuple[int, int], region: EdgeRegion, thresholds: EdgeThresholds
) -> tuple[_Mask, _Mask]:
    """The detection and reference bands for one edge, in card coordinates.

    Each region's depth axis runs inward from its own card edge — detection
    at depth ``[inset, inset + band)``, reference one band deeper — and both
    bands span the edge minus ``corner_exclusion_px`` at either end, which
    is the corner/edge boundary. Built on the card's own grid, so no
    orientation flip exists to undo when the bounding box is placed.
    """
    card_height, card_width = shape
    inset = thresholds.edge_inset_px
    band = thresholds.edge_band_px
    exclusion = thresholds.corner_exclusion_px

    rows, columns = np.mgrid[0:card_height, 0:card_width]
    if region is EdgeRegion.TOP:
        depth, along, length = rows, columns, card_width
    elif region is EdgeRegion.BOTTOM:
        depth, along, length = card_height - 1 - rows, columns, card_width
    elif region is EdgeRegion.LEFT:
        depth, along, length = columns, rows, card_height
    else:
        depth, along, length = card_width - 1 - columns, rows, card_height

    in_run = (along >= exclusion) & (along < length - exclusion)
    detection = (depth >= inset) & (depth < inset + band) & in_run
    reference = (depth >= inset + band) & (depth < inset + 2 * band) & in_run
    return detection, reference


def _classify_edge(
    near_white: _Mask,
    *,
    region: EdgeRegion,
    card_origin: tuple[int, int],
    artifact_size: tuple[int, int],
    thresholds: EdgeThresholds,
) -> RegionFinding:
    detection, reference = _band_masks(near_white.shape, region, thresholds)

    if (near_white & reference).sum() / reference.sum() >= thresholds.white_border_fraction:
        return RegionFinding(
            label=EdgeLabel.UNKNOWN,
            confidence=Confidence.of(_UNKNOWN_CONFIDENCE),
        )

    core = near_white & detection
    area_mm2 = float(core.sum()) / _PX_PER_MM2
    clean_max = thresholds.clean_max_area_mm2
    if area_mm2 <= clean_max:
        margin = min(1.0, (clean_max - area_mm2) / clean_max)
        return RegionFinding(
            label=EdgeLabel.CLEAN,
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
        label=EdgeLabel.WHITENING,
        confidence=Confidence.of(_CONFIDENCE_FLOOR + _CONFIDENCE_SPAN * margin),
        severity=severity,
        bounding_box=_core_box(core, card_origin=card_origin, artifact_size=artifact_size),
    )


def _core_box(
    core: _Mask,
    *,
    card_origin: tuple[int, int],
    artifact_size: tuple[int, int],
) -> BoundingBox:
    """The tight box around the detected core, as artifact fractions (§17).

    `core` is non-empty by construction — the caller only asks past the
    clean threshold — and it already lies in card coordinates, so the card's
    origin alone places the box in the artifact.
    """
    rows, columns = np.nonzero(core)
    row_first, row_last = int(rows.min()), int(rows.max())
    column_first, column_last = int(columns.min()), int(columns.max())

    left, top = card_origin
    width, height = artifact_size
    x = (left + column_first) / width
    y = (top + row_first) / height
    return BoundingBox(
        x=x,
        y=y,
        width=min((column_last - column_first + 1) / width, 1.0 - x),
        height=min((row_last - row_first + 1) / height, 1.0 - y),
    )
