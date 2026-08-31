"""Condition composition — spec §13/§2.2, issue #186.

Needs no database, no object storage and no network, so every claim here is
asserted on every push. The artifacts are **built in the test**, on the rule
the four axis test modules restate: a binary blob in the repository is one
nobody can read at review time.

Two kinds of test share this file. The `assess` tests draw artifacts and
compose the real analyzers; the `compose` tests hand-build domain values,
because the v0.1.0 analyzers can never emit a manufacturing-class finding —
the derivation's tuple path is unreachable through bytes on purpose.

This package is also the first importer of all four analyzer packages, so
the cross-package seam mirrors the sibling doc-comments could only state as
convention are asserted here.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from tcg_domain.analysis import V1_SIDES, ImageSide
from tcg_domain.annotation import (
    CornerLabel,
    CornerRegion,
    DefectSeverity,
    EdgeLabel,
    EdgeRegion,
    SurfaceLabel,
)
from tcg_domain.condition import (
    BoundingBox,
    Centering,
    ConditionAssessment,
    Defect,
    RegionFinding,
    Representation,
    SurfaceAssessment,
)
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain
from tcg_domain.errors import InvalidConditionAssessment
from tcg_ml_centering import CENTERING_VERSION
from tcg_ml_condition import CONDITION_VERSION, assess, compose
from tcg_ml_corners import CORNERS_VERSION, DEFAULT_CORNER_THRESHOLDS
from tcg_ml_corners import classify as classify_corners
from tcg_ml_edges import DEFAULT_EDGE_THRESHOLDS, EDGES_VERSION
from tcg_ml_edges import classify as classify_edges
from tcg_ml_surface import DEFAULT_SURFACE_THRESHOLDS, SURFACE_VERSION

#: The real artifact's numbers (#194): 63 x 88 mm at 12 px/mm, inside a 2 mm
#: margin of real photograph.
CARD_PX_WIDTH, CARD_PX_HEIGHT = 756, 1056
MARGIN_PX = 24
WIDTH, HEIGHT = CARD_PX_WIDTH + 2 * MARGIN_PX, CARD_PX_HEIGHT + 2 * MARGIN_PX

#: Where the card sits in the artifact, as the caller derives it from the
#: stored normalization record — fractions of the unit square.
CARD_FRAME = BoundingBox(
    x=MARGIN_PX / WIDTH,
    y=MARGIN_PX / HEIGHT,
    width=CARD_PX_WIDTH / WIDTH,
    height=CARD_PX_HEIGHT / HEIGHT,
)

#: The photographed surface in the margin, and a flat mid-tone face that is
#: neither the corner/edge axes' near-white nor the surface axis's dark or
#: bright — every axis that looks at it answers, and centering (which needs
#: a printed frame) refuses.
SURFACE_BGR = (90, 90, 90)
FACE_BGR = (140, 150, 160)

#: The centering test module's drawn-frame tones: a bright printed border
#: around checkered content, so `measure` finds the frame.
BORDER, PAPER, INK = 210, 140, 40

#: The four surface classes plus the edge class the issue names as
#: manufacturing by nature.
MANUFACTURING_SURFACE_LABELS = (
    SurfaceLabel.FACTORY_DEFECT,
    SurfaceLabel.REGISTRATION_ISSUE,
    SurfaceLabel.PRINT_LINE,
    SurfaceLabel.PRINT_DOT,
)


def a_plain_card() -> NDArray[np.uint8]:
    """An artifact: grey margin, one flat mid-tone card face, no frame."""
    picture = np.empty((HEIGHT, WIDTH, 3), np.uint8)
    picture[:, :] = SURFACE_BGR
    the_card(picture)[:, :] = FACE_BGR
    return picture


def a_framed_card(
    *, left: int = 36, right: int = 36, top: int = 36, bottom: int = 36
) -> NDArray[np.uint8]:
    """The centering test module's artifact: a bright printed border of the
    given widths around checkered content, so the frame is measurable."""
    rng = np.random.default_rng(7)
    surface = rng.integers(80, 100, (HEIGHT, WIDTH), np.uint8)
    picture = np.stack([surface] * 3, axis=2)
    card = the_card(picture)
    card[:, :] = BORDER
    content = card[top : CARD_PX_HEIGHT - bottom, left : CARD_PX_WIDTH - right]
    content[:, :] = PAPER
    rows, columns = np.mgrid[0 : content.shape[0], 0 : content.shape[1]]
    content[(((rows // 40) + (columns // 40)) % 2).astype(bool)] = INK
    return picture


def the_card(picture: NDArray[np.uint8]) -> NDArray[np.uint8]:
    return picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]


def encoded(picture: NDArray[np.uint8]) -> bytes:
    ok, data = cv2.imencode(".png", picture)
    assert ok
    return bytes(data.tobytes())


def assessed(front: NDArray[np.uint8], back: NDArray[np.uint8]) -> ConditionAssessment:
    result = assess(
        encoded(front), encoded(back), front_card_frame=CARD_FRAME, back_card_frame=CARD_FRAME
    )
    assert isinstance(result, ConditionAssessment)
    return result


def corner_findings(
    *, label: CornerLabel = CornerLabel.CLEAN, confidence: float = 0.95
) -> dict[CornerRegion, RegionFinding]:
    severity = None if label in (CornerLabel.CLEAN, CornerLabel.UNKNOWN) else DefectSeverity.MINOR
    return {
        region: RegionFinding(label=label, confidence=Confidence.of(confidence), severity=severity)
        for region in CornerRegion
    }


def edge_findings(
    *, label: EdgeLabel = EdgeLabel.CLEAN, confidence: float = 0.95
) -> dict[EdgeRegion, RegionFinding]:
    severity = None if label in (EdgeLabel.CLEAN, EdgeLabel.UNKNOWN) else DefectSeverity.MINOR
    return {
        region: RegionFinding(label=label, confidence=Confidence.of(confidence), severity=severity)
        for region in EdgeRegion
    }


_NO_FRAME = InsufficientInformation("no_frame")


def composed(
    *,
    centering: Uncertain[Centering] = _NO_FRAME,
    corners: dict[ImageSide, Uncertain[dict[CornerRegion, RegionFinding]]] | None = None,
    edges: dict[ImageSide, Uncertain[dict[EdgeRegion, RegionFinding]]] | None = None,
    surface: dict[ImageSide, Uncertain[SurfaceAssessment]] | None = None,
) -> ConditionAssessment:
    result = compose(
        centering=centering,
        corners=corners if corners is not None else {side: corner_findings() for side in V1_SIDES},
        edges=edges if edges is not None else {side: edge_findings() for side in V1_SIDES},
        surface=surface
        if surface is not None
        else {side: SurfaceAssessment(findings=()) for side in V1_SIDES},
    )
    assert isinstance(result, ConditionAssessment)
    return result


# --- the cross-package seam mirrors (the first importer's assertions) ------


def test_the_corner_edge_seam_mirrors() -> None:
    """Edges exclude exactly the corner analyzer's crop from each end of
    every run — the 84 px boundary both doc-comments state as convention and
    no single package can test."""
    assert DEFAULT_EDGE_THRESHOLDS.corner_exclusion_px == DEFAULT_CORNER_THRESHOLDS.corner_size_px


def test_the_surface_border_seam_mirrors() -> None:
    """Surface's excluded strip is exactly the edge analyzer's inset plus
    both bands — near-white there is the edge and corner axes' signal."""
    assert (
        DEFAULT_SURFACE_THRESHOLDS.border_exclusion_px
        == DEFAULT_EDGE_THRESHOLDS.edge_inset_px + 2 * DEFAULT_EDGE_THRESHOLDS.edge_band_px
    )


# --- the composed version --------------------------------------------------


def test_the_condition_version_composes_every_axis_version() -> None:
    """The string #187 records as `model_bundle_version` is composed from the
    axis constants rather than hand-maintained, so a package bump cannot be
    forgotten — and it carries the composition logic's own component, so a
    change to these rules bumps the recorded bundle even when no axis moved."""
    assert CONDITION_VERSION.startswith("condition-compose-v")
    assert CENTERING_VERSION in CONDITION_VERSION
    assert CORNERS_VERSION in CONDITION_VERSION
    assert EDGES_VERSION in CONDITION_VERSION
    assert SURFACE_VERSION in CONDITION_VERSION


# --- assess: composing the real analyzers ----------------------------------


def test_a_missing_axis_does_not_sink_the_assessment() -> None:
    """The issue's key requirement: on a flat card centering finds no frame
    and refuses, and the assessment still composes from the axes that
    answered."""
    assessment = assessed(a_plain_card(), a_plain_card())

    assert isinstance(assessment.centering, InsufficientInformation)
    for side in V1_SIDES:
        assert not isinstance(assessment.corners[side], InsufficientInformation)
        assert not isinstance(assessment.edges[side], InsufficientInformation)
        assert not isinstance(assessment.surface[side], InsufficientInformation)


def test_a_framed_card_measures_centering() -> None:
    assessment = assessed(a_framed_card(), a_framed_card())

    assert isinstance(assessment.centering, Centering)
    assert isinstance(assessment.centering.front_horizontal, float)
    assert isinstance(assessment.centering.back_horizontal, float)


def test_the_mappings_are_total_over_the_v1_sides() -> None:
    assessment = assessed(a_plain_card(), a_plain_card())

    assert set(assessment.corners.keys()) == set(V1_SIDES)
    assert set(assessment.edges.keys()) == set(V1_SIDES)
    assert set(assessment.surface.keys()) == set(V1_SIDES)


def test_the_back_side_reaches_the_surface_analyzer_as_the_back() -> None:
    """`classify` is the one analyzer that takes the side, because a
    `Defect` names its side — a stain drawn on the back input must come back
    keyed BACK and carrying BACK."""
    back = a_plain_card()
    the_card(back)[500:548, 350:398] = (30, 30, 30)

    assessment = assessed(a_plain_card(), back)

    back_surface = assessment.surface[ImageSide.BACK]
    assert isinstance(back_surface, SurfaceAssessment)
    assert len(back_surface.findings) == 1
    assert back_surface.findings[0].type is SurfaceLabel.STAIN
    assert back_surface.findings[0].side is ImageSide.BACK


def test_the_overall_confidence_is_the_minimum_the_analyzers_returned() -> None:
    """`min` over every confidence the answered members carry, never a
    product (M5's rule) — asserted comparatively against the analyzers' own
    output for the same bytes."""
    data = encoded(a_plain_card())

    assessment = assessed(a_plain_card(), a_plain_card())

    corners = classify_corners(data, card_frame=CARD_FRAME)
    edges = classify_edges(data, card_frame=CARD_FRAME)
    assert not isinstance(corners, InsufficientInformation)
    assert not isinstance(edges, InsufficientInformation)
    expected = min(
        [finding.confidence for finding in corners.values()]
        + [finding.confidence for finding in edges.values()]
    )
    assert assessment.confidence == expected


def test_undecodable_bytes_on_both_sides_refuse_the_assessment() -> None:
    """When nothing anywhere carries a confidence there is no honest number
    for the required overall one — the only refusal path (#91: not measured
    is never 0%)."""
    result = assess(
        b"not an image", b"not an image", front_card_frame=CARD_FRAME, back_card_frame=CARD_FRAME
    )

    assert isinstance(result, InsufficientInformation)
    assert result.reason == "no_axis_measured"


def test_an_undecodable_front_still_composes_from_the_back() -> None:
    result = assess(
        b"not an image",
        encoded(a_framed_card()),
        front_card_frame=CARD_FRAME,
        back_card_frame=CARD_FRAME,
    )

    assert isinstance(result, ConditionAssessment)
    assert isinstance(result.corners[ImageSide.FRONT], InsufficientInformation)
    assert not isinstance(result.corners[ImageSide.BACK], InsufficientInformation)
    assert isinstance(result.centering, Centering)
    assert isinstance(result.centering.front_horizontal, InsufficientInformation)
    assert isinstance(result.centering.back_horizontal, float)


# --- compose: the derivations the v0.1.0 analyzers cannot reach ------------


def test_the_overall_confidence_is_the_exact_minimum() -> None:
    corners = {
        ImageSide.FRONT: corner_findings(label=CornerLabel.WHITENING, confidence=0.62),
        ImageSide.BACK: corner_findings(confidence=0.9),
    }

    assessment = composed(corners=corners)

    assert assessment.confidence == Confidence.of(0.62)


def test_eye_appeal_is_always_the_refusal() -> None:
    """§13 names it, nothing defines or annotates it — fabricating it would
    violate §2.7. The member exists and answers honestly."""
    assessment = composed()

    assert isinstance(assessment.eye_appeal, InsufficientInformation)
    assert assessment.eye_appeal.reason == "eye_appeal_not_measured_in_v1"


def test_everything_refused_is_the_composition_refusal() -> None:
    refusal = InsufficientInformation("undecodable")
    result = compose(
        centering=refusal,
        corners=dict.fromkeys(V1_SIDES, refusal),
        edges=dict.fromkeys(V1_SIDES, refusal),
        surface=dict.fromkeys(V1_SIDES, refusal),
    )

    assert isinstance(result, InsufficientInformation)
    assert result.reason == "no_axis_measured"


def test_a_mapping_missing_a_side_is_the_domain_refusal_not_a_key_error() -> None:
    """`compose` reads the mappings before the constructor validates them, so
    it guards totality itself — a partial replay through the public seam
    (#188's) must get the domain's own error, never a bare `KeyError`."""
    with pytest.raises(InvalidConditionAssessment, match="exactly the V1 sides"):
        compose(
            centering=_NO_FRAME,
            corners={ImageSide.FRONT: corner_findings()},
            edges={side: edge_findings() for side in V1_SIDES},
            surface={side: SurfaceAssessment(findings=()) for side in V1_SIDES},
        )


def test_a_stray_side_is_refused_even_when_everything_else_is() -> None:
    """The guard runs before the empty-confidences early return — an
    all-refused input with a junk key is malformed, not `no_axis_measured`."""
    refusal = InsufficientInformation("undecodable")
    with pytest.raises(InvalidConditionAssessment, match="exactly the V1 sides"):
        compose(
            centering=refusal,
            corners=dict.fromkeys(V1_SIDES, refusal),
            edges=dict.fromkeys((*V1_SIDES, ImageSide.ANGLED_FRONT), refusal),
            surface=dict.fromkeys(V1_SIDES, refusal),
        )


def test_a_found_surface_manufacturing_defect_is_collected() -> None:
    """The member collects; nothing runs twice — a `factory_defect` in the
    surface findings appears in `manufacturing_defects`, even while another
    feeding class stays refused."""
    defect = Defect(
        type=SurfaceLabel.FACTORY_DEFECT,
        confidence=Confidence.of(0.8),
        severity=DefectSeverity.MODERATE,
        side=ImageSide.FRONT,
        representation=Representation.NORMALIZED,
    )
    surface = {
        ImageSide.FRONT: SurfaceAssessment(
            findings=(defect,),
            not_assessed={SurfaceLabel.PRINT_LINE: InsufficientInformation("adr-0010")},
        ),
        ImageSide.BACK: SurfaceAssessment(findings=()),
    }

    assessment = composed(surface=surface)

    assert assessment.manufacturing_defects == (defect,)


def test_a_rough_cut_edge_converts_to_a_manufacturing_defect() -> None:
    """`rough_cut` is why `Defect.type` includes `EdgeLabel` — the
    conversion carries the side from the mapping key and names the
    normalized frame."""
    finding = RegionFinding(
        label=EdgeLabel.ROUGH_CUT,
        confidence=Confidence.of(0.7),
        severity=DefectSeverity.SEVERE,
    )
    edges = {
        ImageSide.FRONT: edge_findings(),
        ImageSide.BACK: {**edge_findings(), EdgeRegion.LEFT: finding},
    }

    assessment = composed(edges=edges)

    assert not isinstance(assessment.manufacturing_defects, InsufficientInformation)
    assert len(assessment.manufacturing_defects) == 1
    converted = assessment.manufacturing_defects[0]
    assert converted.type is EdgeLabel.ROUGH_CUT
    assert converted.side is ImageSide.BACK
    assert converted.severity is DefectSeverity.SEVERE
    assert converted.confidence == Confidence.of(0.7)
    assert converted.representation is Representation.NORMALIZED
    assert converted.bounding_box is None


@pytest.mark.parametrize("label", MANUFACTURING_SURFACE_LABELS)
def test_a_refused_feeding_class_makes_the_member_insufficient(label: SurfaceLabel) -> None:
    """A derived empty tuple would claim "no manufacturing defects" about
    classes nothing looked at — v0.1.0's surface refuses all four feeding
    classes, so in practice this member is the honest refusal."""
    surface = {
        ImageSide.FRONT: SurfaceAssessment(
            findings=(), not_assessed={label: InsufficientInformation("adr-0010")}
        ),
        ImageSide.BACK: SurfaceAssessment(findings=()),
    }

    assessment = composed(surface=surface)

    assert isinstance(assessment.manufacturing_defects, InsufficientInformation)
    assert assessment.manufacturing_defects.reason == "manufacturing_classes_not_assessed"


def test_a_refused_surface_side_makes_the_member_insufficient() -> None:
    surface: dict[ImageSide, Uncertain[SurfaceAssessment]] = {
        ImageSide.FRONT: InsufficientInformation("undecodable"),
        ImageSide.BACK: SurfaceAssessment(findings=()),
    }

    assessment = composed(surface=surface)

    assert isinstance(assessment.manufacturing_defects, InsufficientInformation)


def test_a_refused_edges_side_makes_the_member_insufficient() -> None:
    edges: dict[ImageSide, Uncertain[dict[EdgeRegion, RegionFinding]]] = {
        ImageSide.FRONT: InsufficientInformation("undecodable"),
        ImageSide.BACK: edge_findings(),
    }

    assessment = composed(edges=edges)

    assert isinstance(assessment.manufacturing_defects, InsufficientInformation)


def test_everything_assessed_and_nothing_found_is_an_empty_tuple() -> None:
    """When every feeding class was actually assessed — no refusals anywhere
    — the empty tuple is a real claim, not a gap."""
    assessment = composed()

    assert assessment.manufacturing_defects == ()
