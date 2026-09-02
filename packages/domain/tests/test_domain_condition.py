"""Unit tests for the neutral condition representation — spec §13, §17, #180.

The shapes are transcriptions of the specification, so the tests are
transcriptions too: §13's tree members, §17's fields and the two coordinate
frames are written out again here rather than derived from the code under test.

Three of the assertions below are about what the shapes must *refuse*, and they
are the ones most likely to be "fixed" by a later reader:

* A bare string cannot name a defect type or a finding label — `"whitening"`
  is in two vocabularies, so a string cannot be checked against the right one.
* Only a surface defect may declare the `original` frame — the mirror of the
  database's `only_a_surface_marks_the_original` CHECK (#175).
* `eye_appeal` is the refusal itself in V1 (epic #8's decomposition decision):
  nothing can measure it, so nothing may claim to have.
"""

from __future__ import annotations

from dataclasses import fields

import pytest
from tcg_domain.analysis import ImageSide
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
    region_coverage,
    surface_coverage,
)
from tcg_domain.confidence import (
    INSUFFICIENT_INFORMATION,
    Confidence,
    InsufficientInformation,
)
from tcg_domain.errors import InvalidConditionAssessment

# Spec §13's tree, in the specification's order.
SECTION_13_TREE = (
    "centering",
    "corners",
    "edges",
    "surface",
    "manufacturing_defects",
    "eye_appeal",
    "confidence",
)

# Spec §17's seven fields, in the specification's order, plus `representation`
# — #175's frame discriminator, which epic #8 applies to predictions exactly as
# to annotations. It sits after `side` because every field before a default is
# required, and a frame nobody named must be refused, not defaulted.
SECTION_17_FIELDS = (
    "type",
    "confidence",
    "severity",
    "side",
    "representation",
    "bounding_box",
    "polygon",
    "metadata",
)

# Spec §13's centering block: four ratios and a confidence.
SECTION_13_CENTERING = (
    "front_horizontal",
    "front_vertical",
    "back_horizontal",
    "back_vertical",
    "confidence",
)

# The two frames a coordinate may be a fraction of — byte-identical to the
# database's pair (`image_annotations.representation`, #175).
THE_TWO_FRAMES = ("normalized", "original")

# ADR 0010's fine surface classes: below the artifact's sampling limit, so a
# model must record them as refused rather than silently omit them.
ADR_0010_FINE_CLASSES = ("scratch", "print_line", "print_dot", "gloss_issue")


# ----------------------------------------------------------------
# Builders — one valid value per shape, overridden per test
# ----------------------------------------------------------------


def sure() -> Confidence:
    return Confidence(0.9)


def a_stain(side: ImageSide = ImageSide.FRONT) -> Defect:
    return Defect(
        type=SurfaceLabel.STAIN,
        confidence=sure(),
        severity=DefectSeverity.MINOR,
        side=side,
        representation=Representation.NORMALIZED,
    )


def sound_corners() -> dict[CornerRegion, RegionFinding]:
    return {
        region: RegionFinding(label=CornerLabel.CLEAN, confidence=sure()) for region in CornerRegion
    }


def sound_edges() -> dict[EdgeRegion, RegionFinding]:
    return {
        region: RegionFinding(label=EdgeLabel.CLEAN, confidence=sure()) for region in EdgeRegion
    }


def measured_centering() -> Centering:
    return Centering(
        front_horizontal=0.55,
        front_vertical=0.48,
        back_horizontal=0.5,
        back_vertical=0.5,
        confidence=sure(),
    )


def assessment(**overrides: object) -> ConditionAssessment:
    values: dict[str, object] = {
        "centering": measured_centering(),
        "corners": {ImageSide.FRONT: sound_corners(), ImageSide.BACK: sound_corners()},
        "edges": {ImageSide.FRONT: sound_edges(), ImageSide.BACK: sound_edges()},
        "surface": {
            ImageSide.FRONT: SurfaceAssessment(findings=()),
            ImageSide.BACK: SurfaceAssessment(findings=()),
        },
        "manufacturing_defects": (),
        "eye_appeal": INSUFFICIENT_INFORMATION,
        "confidence": Confidence(0.8),
    }
    values.update(overrides)
    return ConditionAssessment(**values)  # type: ignore[arg-type]


# ----------------------------------------------------------------
# Transcriptions
# ----------------------------------------------------------------


def test_the_assessment_is_section_13s_tree() -> None:
    """§13's seven members, in the specification's order, and no others."""
    assert tuple(f.name for f in fields(ConditionAssessment)) == SECTION_13_TREE


def test_a_defect_carries_section_17s_seven_fields_and_names_its_frame() -> None:
    """§17's fields verbatim, plus the frame — #175's discriminator.

    Spatial data is captured from day one even though visualization is post-V1,
    and a coordinate that does not say what its fractions are fractions *of* is
    not spatial data.
    """
    assert tuple(f.name for f in fields(Defect)) == SECTION_17_FIELDS


def test_centering_is_four_ratios_and_a_confidence() -> None:
    """§13's centering block: ratios, never only qualitative labels."""
    assert tuple(f.name for f in fields(Centering)) == SECTION_13_CENTERING


def test_the_two_frames_are_normalized_and_original() -> None:
    """Byte-identical to `image_annotations.representation`'s pair (#175).

    The service's constants are deliberately not importable from the domain, so
    the spelling is duplicated on purpose — this transcription is what keeps
    the two from drifting.
    """
    assert tuple(frame.value for frame in Representation) == THE_TWO_FRAMES


# ----------------------------------------------------------------
# Defect
# ----------------------------------------------------------------


def test_only_a_surface_defect_marks_the_original() -> None:
    """The mirror of `only_a_surface_marks_the_original` (#175, ADR 0010).

    The fine-class route back is surface annotations against the original
    photograph; an edge defect there would name a frame its analyzer never saw.
    """
    scratch = Defect(
        type=SurfaceLabel.SCRATCH,
        confidence=sure(),
        severity=DefectSeverity.MINOR,
        side=ImageSide.FRONT,
        representation=Representation.ORIGINAL,
    )
    assert scratch.representation is Representation.ORIGINAL

    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type=EdgeLabel.ROUGH_CUT,
            confidence=sure(),
            severity=DefectSeverity.MODERATE,
            side=ImageSide.FRONT,
            representation=Representation.ORIGINAL,
        )


def test_a_bare_string_cannot_name_a_defect_type() -> None:
    """`"dent"` is in three vocabularies; a string cannot pick the right one."""
    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type="stain",  # type: ignore[arg-type]
            confidence=sure(),
            severity=DefectSeverity.MINOR,
            side=ImageSide.FRONT,
            representation=Representation.NORMALIZED,
        )


def test_clean_is_not_a_defect() -> None:
    """A defect asserting no defect is a contradiction, not a finding.

    `clean` corners and edges travel as :class:`RegionFinding`; a clean surface
    is an empty findings tuple (§16's asymmetry).
    """
    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type=EdgeLabel.CLEAN,
            confidence=sure(),
            severity=None,
            side=ImageSide.FRONT,
            representation=Representation.NORMALIZED,
        )


def test_an_unknown_defect_carries_no_severity_and_a_named_one_must() -> None:
    """§17 requires a severity beside a defect; `unknown` could not rate one.

    The same pairing `ck_annotations_a_defect_carries_a_severity` enforces on
    annotation rows, both directions.
    """
    unknown = Defect(
        type=SurfaceLabel.UNKNOWN,
        confidence=sure(),
        severity=None,
        side=ImageSide.FRONT,
        representation=Representation.NORMALIZED,
    )
    assert unknown.severity is None

    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type=SurfaceLabel.UNKNOWN,
            confidence=sure(),
            severity=DefectSeverity.MINOR,
            side=ImageSide.FRONT,
            representation=Representation.NORMALIZED,
        )
    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type=SurfaceLabel.STAIN,
            confidence=sure(),
            severity=None,
            side=ImageSide.FRONT,
            representation=Representation.NORMALIZED,
        )


def test_a_bounding_box_lies_inside_the_unit_square_and_has_area() -> None:
    """Fractions of the declared frame — `_BOX_LIES_INSIDE_THE_ARTIFACT`'s rule."""
    box = BoundingBox(x=0.1, y=0.2, width=0.3, height=0.4)
    assert (box.x, box.y, box.width, box.height) == (0.1, 0.2, 0.3, 0.4)

    for bad in (
        {"x": -0.1, "y": 0.0, "width": 0.5, "height": 0.5},
        {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.5},
        {"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.0},
        {"x": 0.6, "y": 0.0, "width": 0.5, "height": 0.5},
        {"x": 0.0, "y": 0.6, "width": 0.5, "height": 0.5},
        {"x": 0.0, "y": 0.0, "width": 1.5, "height": 0.5},
    ):
        with pytest.raises(InvalidConditionAssessment):
            BoundingBox(**bad)


def test_a_polygon_has_at_least_three_points_in_the_unit_square() -> None:
    """§17's polygon, in the same fractional space as the bounding box."""
    triangle = ((0.1, 0.1), (0.5, 0.1), (0.3, 0.4))
    dented = a_stain()
    with_polygon = Defect(
        type=SurfaceLabel.STAIN,
        confidence=sure(),
        severity=DefectSeverity.MINOR,
        side=ImageSide.FRONT,
        representation=Representation.NORMALIZED,
        polygon=triangle,
    )
    assert with_polygon.polygon == triangle
    assert dented.polygon is None

    for bad in (
        ((0.1, 0.1), (0.5, 0.1)),
        ((0.1, 0.1), (0.5, 0.1), (0.3, 1.4)),
    ):
        with pytest.raises(InvalidConditionAssessment):
            Defect(
                type=SurfaceLabel.STAIN,
                confidence=sure(),
                severity=DefectSeverity.MINOR,
                side=ImageSide.FRONT,
                representation=Representation.NORMALIZED,
                polygon=bad,
            )


def test_metadata_is_frozen_and_copied() -> None:
    """§17's open bag, but never a mutable back door into a frozen value."""
    source: dict[str, object] = {"detector": "edge-band"}
    defect = Defect(
        type=SurfaceLabel.SCUFF,
        confidence=sure(),
        severity=DefectSeverity.MINOR,
        side=ImageSide.FRONT,
        representation=Representation.NORMALIZED,
        metadata=source,
    )
    source["detector"] = "changed"
    assert defect.metadata["detector"] == "edge-band"
    with pytest.raises(TypeError):
        defect.metadata["detector"] = "changed"  # type: ignore[index]


def test_a_corner_label_cannot_name_a_defect() -> None:
    """`Defect.type` deliberately excludes `CornerLabel` (epic decision 3):
    corners travel as :class:`RegionFinding`, and `manufacturing_defects`
    derives from surface and edge findings only.
    """
    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type=CornerLabel.WHITENING,  # type: ignore[arg-type]
            confidence=sure(),
            severity=DefectSeverity.MINOR,
            side=ImageSide.FRONT,
            representation=Representation.NORMALIZED,
        )


def test_a_side_or_frame_the_domain_does_not_know_is_the_domain_error() -> None:
    """errors.py's invariant: the whole domain is catchable with one clause,
    so a bad coercion must never escape as a bare ValueError.
    """
    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type=SurfaceLabel.STAIN,
            confidence=sure(),
            severity=DefectSeverity.MINOR,
            side="sideways",  # type: ignore[arg-type]
            representation=Representation.NORMALIZED,
        )
    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type=SurfaceLabel.STAIN,
            confidence=sure(),
            severity=DefectSeverity.MINOR,
            side=ImageSide.FRONT,
            representation="warped",  # type: ignore[arg-type]
        )


def test_a_malformed_polygon_point_is_the_domain_error() -> None:
    """A point that is not an (x, y) pair is refused, never a TypeError."""
    with pytest.raises(InvalidConditionAssessment):
        Defect(
            type=SurfaceLabel.STAIN,
            confidence=sure(),
            severity=DefectSeverity.MINOR,
            side=ImageSide.FRONT,
            representation=Representation.NORMALIZED,
            polygon=(1, 2, 3),  # type: ignore[arg-type]
        )


def test_a_fraction_must_be_a_finite_real() -> None:
    """NaN measures nothing, infinity lies, and True is not a number."""
    with pytest.raises(InvalidConditionAssessment):
        BoundingBox(x=float("nan"), y=0.0, width=0.5, height=0.5)
    with pytest.raises(InvalidConditionAssessment):
        BoundingBox(x=True, y=0.0, width=0.5, height=0.5)
    with pytest.raises(InvalidConditionAssessment):
        Centering(
            front_horizontal=float("inf"),
            front_vertical=0.5,
            back_horizontal=0.5,
            back_vertical=0.5,
            confidence=sure(),
        )


# ----------------------------------------------------------------
# RegionFinding
# ----------------------------------------------------------------


def test_a_finding_carries_section_14s_four_answers() -> None:
    """§14: classification, confidence, severity, spatial information where available."""
    assert tuple(f.name for f in fields(RegionFinding)) == (
        "label",
        "confidence",
        "severity",
        "bounding_box",
    )


def test_a_no_defect_label_carries_no_severity() -> None:
    """`clean` found nothing to rate; `unknown` could not rate what it found."""
    clean = RegionFinding(label=CornerLabel.CLEAN, confidence=sure())
    assert clean.severity is None

    with pytest.raises(InvalidConditionAssessment):
        RegionFinding(label=CornerLabel.CLEAN, confidence=sure(), severity=DefectSeverity.MINOR)
    with pytest.raises(InvalidConditionAssessment):
        RegionFinding(label=EdgeLabel.UNKNOWN, confidence=sure(), severity=DefectSeverity.MINOR)
    with pytest.raises(InvalidConditionAssessment):
        RegionFinding(label=CornerLabel.WHITENING, confidence=sure(), severity=None)


def test_a_rough_cut_edge_needs_no_bounding_box() -> None:
    """§15's `rough_cut` is a whole-edge property; the honest spatial claim is
    "this edge", and the shape must not force a box where none is meant (#184).
    """
    finding = RegionFinding(
        label=EdgeLabel.ROUGH_CUT,
        confidence=sure(),
        severity=DefectSeverity.MODERATE,
    )
    assert finding.bounding_box is None


def test_a_bare_string_cannot_name_a_label() -> None:
    """`"whitening"` is a corner label and an edge label; a string picks neither."""
    with pytest.raises(InvalidConditionAssessment):
        RegionFinding(label="whitening", confidence=sure(), severity=DefectSeverity.MINOR)  # type: ignore[arg-type]


# ----------------------------------------------------------------
# Centering
# ----------------------------------------------------------------


def test_a_ratio_lies_in_the_unit_interval() -> None:
    """A centering ratio is border/(border+opposite); 1.5 measures nothing."""
    with pytest.raises(InvalidConditionAssessment):
        Centering(
            front_horizontal=1.5,
            front_vertical=0.5,
            back_horizontal=0.5,
            back_vertical=0.5,
            confidence=sure(),
        )


def test_a_borderless_axis_may_be_insufficient_information() -> None:
    """§21's template-awareness: a card with no measurable border on one axis
    still reports the other (#182; the DB's per-axis nullable ratios agree).
    """
    partial = Centering(
        front_horizontal=0.55,
        front_vertical=InsufficientInformation("borderless vertical"),
        back_horizontal=0.5,
        back_vertical=0.5,
        confidence=sure(),
    )
    assert isinstance(partial.front_vertical, InsufficientInformation)


def test_centering_with_nothing_measured_is_not_constructible() -> None:
    """A confidence over zero measured ratios is a confidence about nothing.

    The whole-axis refusal is spelled `centering=INSUFFICIENT_INFORMATION` on
    the assessment, uniformly with every other axis.
    """
    with pytest.raises(InvalidConditionAssessment):
        Centering(
            front_horizontal=INSUFFICIENT_INFORMATION,
            front_vertical=INSUFFICIENT_INFORMATION,
            back_horizontal=INSUFFICIENT_INFORMATION,
            back_vertical=INSUFFICIENT_INFORMATION,
            confidence=sure(),
        )


# ----------------------------------------------------------------
# SurfaceAssessment
# ----------------------------------------------------------------


def test_a_clean_surface_is_an_empty_findings_tuple() -> None:
    """§16 has no `clean` class: a clean surface is the absence of findings."""
    clean = SurfaceAssessment(findings=())
    assert clean.findings == ()
    assert dict(clean.not_assessed) == {}


def test_a_fine_class_refusal_is_recorded_not_omitted() -> None:
    """ADR 0010: the fine classes are below the artifact's sampling limit and
    must be answered `insufficient_information` class-level, never dropped (#185).
    """
    refused = {
        SurfaceLabel(name): InsufficientInformation("below the artifact's sampling limit")
        for name in ADR_0010_FINE_CLASSES
    }
    surface = SurfaceAssessment(findings=(a_stain(),), not_assessed=refused)
    assert set(surface.not_assessed) == set(refused)
    for verdict in surface.not_assessed.values():
        assert isinstance(verdict, InsufficientInformation)


def test_a_class_cannot_be_found_and_not_assessed_at_once() -> None:
    """One class, one answer: a stain both found and refused is two answers."""
    with pytest.raises(InvalidConditionAssessment):
        SurfaceAssessment(
            findings=(a_stain(),),
            not_assessed={SurfaceLabel.STAIN: INSUFFICIENT_INFORMATION},
        )


def test_a_surface_finding_speaks_the_surface_vocabulary() -> None:
    """An edge defect under `surface` is a category error, not a finding."""
    rough = Defect(
        type=EdgeLabel.ROUGH_CUT,
        confidence=sure(),
        severity=DefectSeverity.MODERATE,
        side=ImageSide.FRONT,
        representation=Representation.NORMALIZED,
    )
    with pytest.raises(InvalidConditionAssessment):
        SurfaceAssessment(findings=(rough,))


def test_one_assessment_describes_one_photograph() -> None:
    """A surface assessment is per side; mixed sides mean a composition bug."""
    with pytest.raises(InvalidConditionAssessment):
        SurfaceAssessment(findings=(a_stain(ImageSide.FRONT), a_stain(ImageSide.BACK)))


# ----------------------------------------------------------------
# ConditionAssessment
# ----------------------------------------------------------------


def test_the_corner_and_edge_mappings_are_total() -> None:
    """`image_quality`'s completeness rule: a region nobody assessed cannot be
    silently dropped — `unknown` is how an analyzer says it could not judge one.
    """
    three_corners = sound_corners()
    del three_corners[CornerRegion.TOP_LEFT]
    with pytest.raises(InvalidConditionAssessment):
        assessment(corners={ImageSide.FRONT: three_corners, ImageSide.BACK: sound_corners()})

    with pytest.raises(InvalidConditionAssessment):
        assessment(corners={ImageSide.FRONT: sound_corners()})

    with pytest.raises(InvalidConditionAssessment):
        assessment(
            corners={
                ImageSide.FRONT: sound_corners(),
                ImageSide.BACK: sound_corners(),
                ImageSide.ANGLED_FRONT: sound_corners(),
            }
        )


def test_a_refused_axis_leaves_the_assessment_constructible() -> None:
    """#186's partial evidence: M8's models take what answered and widen.

    One refused axis — or all of them — must not turn into a missing assessment.
    """
    partial = assessment(
        centering=INSUFFICIENT_INFORMATION,
        corners={
            ImageSide.FRONT: sound_corners(),
            ImageSide.BACK: InsufficientInformation("back photograph unusable"),
        },
        surface={
            ImageSide.FRONT: SurfaceAssessment(findings=()),
            ImageSide.BACK: INSUFFICIENT_INFORMATION,
        },
        manufacturing_defects=INSUFFICIENT_INFORMATION,
    )
    assert isinstance(partial.centering, InsufficientInformation)
    assert isinstance(partial.corners[ImageSide.BACK], InsufficientInformation)


def test_a_corner_finding_must_speak_the_corner_vocabulary() -> None:
    """`rough_cut` is an edge defect a corner cannot have — the two lists are
    deliberately not one list, and the enum member is what enforces it here.
    """
    with_edge_label = sound_corners()
    with_edge_label[CornerRegion.TOP_LEFT] = RegionFinding(
        label=EdgeLabel.ROUGH_CUT,
        confidence=sure(),
        severity=DefectSeverity.MODERATE,
    )
    with pytest.raises(InvalidConditionAssessment):
        assessment(corners={ImageSide.FRONT: with_edge_label, ImageSide.BACK: sound_corners()})


def test_an_edge_finding_must_speak_the_edge_vocabulary() -> None:
    """The generic axis check has two callers; both branches are pinned."""
    with_corner_label = sound_edges()
    with_corner_label[EdgeRegion.TOP] = RegionFinding(
        label=CornerLabel.ROUNDING,
        confidence=sure(),
        severity=DefectSeverity.MINOR,
    )
    with pytest.raises(InvalidConditionAssessment):
        assessment(edges={ImageSide.FRONT: with_corner_label, ImageSide.BACK: sound_edges()})


def test_an_unknown_refusal_class_is_the_domain_error() -> None:
    """A `not_assessed` key outside §16's classes is refused as the domain error."""
    with pytest.raises(InvalidConditionAssessment):
        SurfaceAssessment(findings=(), not_assessed={"shine": INSUFFICIENT_INFORMATION})  # type: ignore[dict-item]


def test_a_surface_defect_agrees_with_its_side_key() -> None:
    """A back stain filed under the front's surface is a composition bug."""
    with pytest.raises(InvalidConditionAssessment):
        assessment(
            surface={
                ImageSide.FRONT: SurfaceAssessment(findings=(a_stain(ImageSide.BACK),)),
                ImageSide.BACK: SurfaceAssessment(findings=()),
            }
        )


def test_eye_appeal_is_the_refusal_in_v1() -> None:
    """Epic #8's decomposition decision 2: §13 names it, nothing defines it,
    §30's annotation features do not capture it — fabricating it violates §2.7.
    """
    honest = assessment(eye_appeal=InsufficientInformation("undefined in V1"))
    assert isinstance(honest.eye_appeal, InsufficientInformation)

    with pytest.raises(InvalidConditionAssessment):
        assessment(eye_appeal=Confidence(0.9))


def test_manufacturing_defects_may_carry_an_edge_defect() -> None:
    """Epic decision 3: the member is derived from surface *and edge* findings —
    `rough_cut` is manufacturing by nature, which is why `Defect.type` admits
    `EdgeLabel`.
    """
    rough = Defect(
        type=EdgeLabel.ROUGH_CUT,
        confidence=sure(),
        severity=DefectSeverity.MODERATE,
        side=ImageSide.FRONT,
        representation=Representation.NORMALIZED,
    )
    derived = assessment(manufacturing_defects=(rough, a_stain()))
    assert derived.manufacturing_defects == (rough, a_stain())


def test_a_manufacturing_defect_stays_on_a_v1_side() -> None:
    """The other axes are total over the V1 sides; a derived defect naming a
    side no analyzer produced would be a composition bug wearing a value.
    """
    angled = Defect(
        type=SurfaceLabel.STAIN,
        confidence=sure(),
        severity=DefectSeverity.MINOR,
        side=ImageSide.ANGLED_FRONT,
        representation=Representation.NORMALIZED,
    )
    with pytest.raises(InvalidConditionAssessment):
        assessment(manufacturing_defects=(angled,))


def test_manufacturing_defects_must_be_defects_or_the_refusal() -> None:
    """A non-sequence must be refused as the domain error, never a TypeError."""
    with pytest.raises(InvalidConditionAssessment):
        assessment(manufacturing_defects=5)
    with pytest.raises(InvalidConditionAssessment):
        assessment(manufacturing_defects=("stain",))


def test_the_mappings_are_frozen() -> None:
    """A validated assessment cannot be mutated into an invalid one."""
    built = assessment()
    with pytest.raises(TypeError):
        built.corners[ImageSide.FRONT] = INSUFFICIENT_INFORMATION  # type: ignore[index]
    front_corners = built.corners[ImageSide.FRONT]
    assert not isinstance(front_corners, InsufficientInformation)
    with pytest.raises(TypeError):
        front_corners[CornerRegion.TOP_LEFT] = RegionFinding(  # type: ignore[index]
            label=CornerLabel.CLEAN, confidence=sure()
        )


# ----------------------------------------------------------------
# Coverage — how much of the card an assessment actually looked at (#225)
# ----------------------------------------------------------------
#
# Hoisted out of `ml/grading/psa` and `ml/grading/tag` when BGS's predictor
# would have been the third hand-written copy. What each predictor *does* with
# the fraction stays in that predictor; only the counting is here.


def test_a_fully_read_axis_is_wholly_covered() -> None:
    built = assessment()
    assert region_coverage(built.corners, regions_per_side=len(CornerRegion)) == 1.0
    assert region_coverage(built.edges, regions_per_side=len(EdgeRegion)) == 1.0


def test_an_unknown_region_is_a_slot_seen_and_not_read() -> None:
    """`unknown` is the analyzer saying it could not judge a region it saw — so
    it lowers coverage rather than counting as a clean corner."""
    unreadable = sound_corners()
    unreadable[CornerRegion.TOP_LEFT] = RegionFinding(label=CornerLabel.UNKNOWN, confidence=sure())
    built = assessment(corners={ImageSide.FRONT: unreadable, ImageSide.BACK: sound_corners()})

    assert region_coverage(built.corners, regions_per_side=len(CornerRegion)) == 7 / 8


def test_a_refused_side_is_all_of_its_regions_at_once() -> None:
    built = assessment(
        corners={ImageSide.FRONT: INSUFFICIENT_INFORMATION, ImageSide.BACK: sound_corners()}
    )
    assert region_coverage(built.corners, regions_per_side=len(CornerRegion)) == 0.5

    nothing = assessment(
        corners=dict.fromkeys((ImageSide.FRONT, ImageSide.BACK), INSUFFICIENT_INFORMATION)
    )
    assert region_coverage(nothing.corners, regions_per_side=len(CornerRegion)) == 0.0


def test_an_answered_surface_side_still_reports_what_it_did_not_look_at() -> None:
    """The load-bearing one. ADR 0010's fine classes are refused class-level, so
    an empty `findings` tuple is not a clean card — it is a card whose surface
    was only partly examined, and this says by how much."""
    partial = SurfaceAssessment(
        findings=(),
        not_assessed=dict.fromkeys(
            (SurfaceLabel(name) for name in ADR_0010_FINE_CLASSES), INSUFFICIENT_INFORMATION
        ),
    )
    built = assessment(surface={ImageSide.FRONT: partial, ImageSide.BACK: partial})

    expected = 1.0 - len(ADR_0010_FINE_CLASSES) / len(SurfaceLabel)
    assert surface_coverage(built.surface) == pytest.approx(expected)


def test_surface_coverage_averages_the_two_sides() -> None:
    built = assessment(
        surface={
            ImageSide.FRONT: SurfaceAssessment(findings=()),
            ImageSide.BACK: INSUFFICIENT_INFORMATION,
        }
    )
    assert surface_coverage(built.surface) == 0.5

    nothing = assessment(
        surface=dict.fromkeys((ImageSide.FRONT, ImageSide.BACK), INSUFFICIENT_INFORMATION)
    )
    assert surface_coverage(nothing.surface) == 0.0
