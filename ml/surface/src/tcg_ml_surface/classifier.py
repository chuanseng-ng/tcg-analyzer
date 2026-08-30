"""Surface classification — spec §16/§17, issue #185.

Artifact bytes in, one side's `SurfaceAssessment` out — or
:class:`~tcg_domain.confidence.InsufficientInformation` when the side cannot
be judged at all. `ml/edges` is the model for the shape of this module: no
database, no object storage, no HTTP, and the card rectangle is the frame
the caller names from the artifact's *stored* normalization record, never
found here and never assumed to be the image boundary.

**v0.1.0 claims `stain` and `scuff` — nothing else.** The fine four
(`scratch`, `print_line`, `print_dot`, `gloss_issue`) are below the
12 px/mm artifact's sampling limit and are refused class-level per ADR 0010
— a model reporting fine scratches it cannot see is the confidently-wrong
failure §2.7 forbids, and #175's original-photograph representation is the
only route back to that signal. The other five coarse classes have no
honest classical signal in one normalized view: `dent` and `indentation`
are depth, `color_issue` needs a reference image ADR 0004 says does not
exist, `registration_issue` needs a print template nothing holds, and
`factory_defect` is a judgement. Every refusal is a
`SurfaceAssessment.not_assessed` entry with its own reason, never a
silently omitted class. A learned model enters through `ml/evaluation`'s
benchmark (#188) behind this same interface — epic #8's decision 1 — and
#188 also measures what the restriction costs against the annotated
corpus. `SurfaceLabel.UNKNOWN` labels an unnameable defect, not an
assessable class; v0.1.0 never emits one and never refuses it.

**A stain is a dark blob and a scuff is a dull whitish abrasion** — the
scuff mask is the edge and corner axes' near-white claim on the open face.
Both run only where the face itself is quiet: at or past
``face_busy_fraction`` of busy pixels (the 3x3 Laplacian magnitude over
``laplacian_threshold``) the face's own texture is indistinguishable from
defect texture — the issue's holo clause — and both classes refuse rather
than guess. A surviving candidate whose surrounding annulus is busy sits
inside artwork and is dropped before anything is claimed (#176's
filter-before-selection). **The outer ``border_exclusion_px`` strip is
never read**: it is the edge and corner analyzers' detection and reference
bands, and near-white there is their whitening signal — #184's seam rule,
extended to this axis.

Named ceilings, all #188's to price (ponytail: each names its upgrade
path): the grey and HSV thresholds are absolute, so exposure moves what
counts as dark or near-white — the upgrade is thresholding relative to the
face's own median; the context gate hides a real stain inside busy artwork
and passes a printed dark shape on a plain background — the trade is
priced against `image_annotations`, and a learned model is the upgrade,
not a threshold tweak; foil glare bright enough to read near-white on a
face quiet enough to pass the busy gate can graze a `scuff`; banded area
carries no shape, so a long faint smear and a compact dark spot of equal
area read alike; and the depth band between the edge axes' detection and
this strip's end is claimed by no axis as defect territory — the seam
costs a ring nobody reports.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray
from tcg_domain.analysis import ImageSide
from tcg_domain.annotation import DefectSeverity, SurfaceLabel
from tcg_domain.condition import BoundingBox, Defect, Representation, SurfaceAssessment
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain

from tcg_ml_surface.thresholds import DEFAULT_SURFACE_THRESHOLDS, SurfaceThresholds

__all__ = ["classify"]

#: The artifact's scale is fixed by construction: 12 px/mm, so areas convert
#: at 144 px²/mm² — the same reason `ml/edges` denominates its bands in
#: pixels.
_PX_PER_MM2: Final = 144.0

#: Said when the bytes did not decode. This package answers rather than
#: raising, so the one place undecodable bytes become a job failure stays
#: upstream of it.
_UNDECODABLE: Final = "the artifact could not be decoded"

#: Said when the card frame names a region too small to hold a face inside
#: the border exclusion.
_CARD_TOO_SMALL: Final = "the card frame names a region too small to classify"

#: The class-level refusals v0.1.0 always answers, each with its own reason.
_FINE_CLASS: Final = InsufficientInformation(
    "below the sampling limit of the 12 px/mm artifact (ADR 0010)"
)
_DEPTH_SIGNAL: Final = InsufficientInformation("a depth signal one normalized view does not carry")
_NO_REFERENCE: Final = InsufficientInformation("no reference image to compare against (ADR 0004)")
_NO_TEMPLATE: Final = InsufficientInformation("no print template to measure registration against")
_A_JUDGEMENT: Final = InsufficientInformation("a manufacturing judgement this baseline cannot make")

#: Said of `stain` and `scuff` when the face itself is busy — the holo
#: clause: foil and dense artwork read as defect texture to this baseline.
_BUSY_FACE: Final = InsufficientInformation(
    "the face's own texture is indistinguishable from defect texture in this signal"
)

_ALWAYS_REFUSED: Final[dict[SurfaceLabel, InsufficientInformation]] = {
    SurfaceLabel.SCRATCH: _FINE_CLASS,
    SurfaceLabel.PRINT_LINE: _FINE_CLASS,
    SurfaceLabel.PRINT_DOT: _FINE_CLASS,
    SurfaceLabel.GLOSS_ISSUE: _FINE_CLASS,
    SurfaceLabel.DENT: _DEPTH_SIGNAL,
    SurfaceLabel.INDENTATION: _DEPTH_SIGNAL,
    SurfaceLabel.COLOR_ISSUE: _NO_REFERENCE,
    SurfaceLabel.REGISTRATION_ISSUE: _NO_TEMPLATE,
    SurfaceLabel.FACTORY_DEFECT: _A_JUDGEMENT,
}

#: The confidence recipe: margin-from-threshold, scaled into
#: ``[floor, floor + span]``. A heuristic in [0, 1], not a calibrated
#: probability — §26's calibration work is M8's, and #64 gates on the value
#: downstream, which is why it must not flatter itself.
_CONFIDENCE_FLOOR: Final = 0.5
_CONFIDENCE_SPAN: Final = 0.45

_Mask = NDArray[np.bool_]


def classify(
    data: bytes,
    *,
    side: ImageSide,
    card_frame: BoundingBox,
    thresholds: SurfaceThresholds = DEFAULT_SURFACE_THRESHOLDS,
) -> Uncertain[SurfaceAssessment]:
    """Classify one side's surface from its normalized artifact.

    Args:
        data: The artifact's encoded bytes.
        side: Which side's artifact this is — carried onto every finding,
            because a `Defect` names its side where a `RegionFinding` does
            not, and `ConditionAssessment.surface` checks it against its key.
        card_frame: Where the card sits in the artifact, as fractions of the
            unit square — derived from the artifact's stored normalization
            record. `BoundingBox(0, 0, 1, 1)` is a pre-#194 artifact whose
            card really does reach the edges.
        thresholds: What counts as a stain or a scuff. Replace wholesale or
            not at all.

    Returns:
        A `SurfaceAssessment` — findings for what was seen (a clean face is
        an empty tuple; §16 has no `clean`), `not_assessed` for every class
        this version refuses, each with its reason — or
        `InsufficientInformation` naming why the whole side was not judged.
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
    # some face must remain once the border exclusion is cut from every side.
    if min(card.shape[:2]) < 2 * thresholds.border_exclusion_px + 1:
        return InsufficientInformation(_CARD_TOO_SMALL)

    exclusion = thresholds.border_exclusion_px
    face = np.zeros(card.shape[:2], np.bool_)
    face[exclusion:-exclusion, exclusion:-exclusion] = True

    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    busy = np.abs(cv2.Laplacian(gray, cv2.CV_16S, ksize=1)) > thresholds.laplacian_threshold

    refusals = dict(_ALWAYS_REFUSED)
    if float(busy[face].mean()) >= thresholds.face_busy_fraction:
        refusals[SurfaceLabel.STAIN] = _BUSY_FACE
        refusals[SurfaceLabel.SCUFF] = _BUSY_FACE
        return SurfaceAssessment(findings=(), not_assessed=refusals)

    hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
    stain = (gray <= thresholds.stain_max_value) & face
    scuff = (
        (hsv[:, :, 1] <= thresholds.max_white_saturation)
        & (hsv[:, :, 2] >= thresholds.min_white_value)
        & face
    )

    findings = [
        *_findings(stain, SurfaceLabel.STAIN, busy, side, (left, top), (width, height), thresholds),
        *_findings(scuff, SurfaceLabel.SCUFF, busy, side, (left, top), (width, height), thresholds),
    ]
    return SurfaceAssessment(findings=tuple(findings), not_assessed=refusals)


def _findings(
    candidates: _Mask,
    label: SurfaceLabel,
    busy: _Mask,
    side: ImageSide,
    card_origin: tuple[int, int],
    artifact_size: tuple[int, int],
    thresholds: SurfaceThresholds,
) -> list[Defect]:
    """One polarity's defects: despeckle, connect, gate, claim."""
    opened = cv2.morphologyEx(
        candidates.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(opened)

    defects: list[Defect] = []
    for component in range(1, count):
        column, row, box_width, box_height, area_px = (int(value) for value in stats[component])
        area_mm2 = area_px / _PX_PER_MM2
        if area_mm2 < thresholds.min_defect_area_mm2:
            continue
        if (
            _annulus_busy_fraction(
                busy, (row, column, box_height, box_width), thresholds.context_margin_px
            )
            >= thresholds.context_busy_fraction
        ):
            # Busy surroundings mean artwork, not a mark on it — dropped
            # before anything is claimed (#176's filter-before-selection).
            continue

        if area_mm2 < thresholds.moderate_min_area_mm2:
            severity = DefectSeverity.MINOR
        elif area_mm2 < thresholds.severe_min_area_mm2:
            severity = DefectSeverity.MODERATE
        else:
            severity = DefectSeverity.SEVERE
        floor = thresholds.min_defect_area_mm2
        margin = min(1.0, (area_mm2 - floor) / floor)
        left, top = card_origin
        width, height = artifact_size
        x = (left + column) / width
        y = (top + row) / height
        defects.append(
            Defect(
                type=label,
                confidence=Confidence.of(_CONFIDENCE_FLOOR + _CONFIDENCE_SPAN * margin),
                severity=severity,
                side=side,
                representation=Representation.NORMALIZED,
                bounding_box=BoundingBox(
                    x=x,
                    y=y,
                    width=min(box_width / width, 1.0 - x),
                    height=min(box_height / height, 1.0 - y),
                ),
            )
        )
    return defects


def _annulus_busy_fraction(busy: _Mask, box: tuple[int, int, int, int], margin: int) -> float:
    """The busy fraction of the ring around one candidate's box.

    The ring is the box dilated by ``margin`` and clipped to the card, minus
    the box itself. A ring with no pixels — a candidate nearly the size of
    the card — has no context to condemn it and reads quiet.
    """
    row, column, box_height, box_width = box
    card_height, card_width = busy.shape
    top = max(row - margin, 0)
    bottom = min(row + box_height + margin, card_height)
    left = max(column - margin, 0)
    right = min(column + box_width + margin, card_width)

    ring_area = (bottom - top) * (right - left) - box_height * box_width
    if ring_area <= 0:
        return 0.0
    ring_busy = int(busy[top:bottom, left:right].sum()) - int(
        busy[row : row + box_height, column : column + box_width].sum()
    )
    return float(ring_busy / ring_area)
