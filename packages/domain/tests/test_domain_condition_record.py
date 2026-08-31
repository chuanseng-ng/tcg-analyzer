"""Unit tests for the condition assessment's persisted form — #187.

`ConditionAssessment.as_record()` is `QualityReport.as_record()`'s sibling:
plain JSON types the worker writes to `analyses.condition_details` without a
serializer knowing anything about the domain. The conventions under test are
the family's — `str(enum_member)` for vocabulary, omit-not-null for
optionals, `confidence.value` as a float (never the `"87%"` display string) —
plus one of this record's own: every `InsufficientInformation`, wherever it
sits in the tree, serializes as the same one-key object
`{"insufficient_information": <reason or None>}`, so a reader discriminates
an answer from a refusal by that key and nothing else.

`card_frame_of` moves into the domain here (returning a validated
:class:`BoundingBox`), because #187's worker is its second consumer and the
first outside the datasets domain.
"""

from __future__ import annotations

import json

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
    card_frame_of,
)
from tcg_domain.confidence import (
    INSUFFICIENT_INFORMATION,
    Confidence,
    InsufficientInformation,
)

# ----------------------------------------------------------------
# Builders — one valid value per shape, overridden per test
# ----------------------------------------------------------------


def sure() -> Confidence:
    return Confidence(0.9)


def a_stain(**overrides: object) -> Defect:
    values: dict[str, object] = {
        "type": SurfaceLabel.STAIN,
        "confidence": sure(),
        "severity": DefectSeverity.MINOR,
        "side": ImageSide.FRONT,
        "representation": Representation.NORMALIZED,
    }
    values.update(overrides)
    return Defect(**values)  # type: ignore[arg-type]


def sound_corners() -> dict[CornerRegion, RegionFinding]:
    return {
        region: RegionFinding(label=CornerLabel.CLEAN, confidence=sure()) for region in CornerRegion
    }


def sound_edges() -> dict[EdgeRegion, RegionFinding]:
    return {
        region: RegionFinding(label=EdgeLabel.CLEAN, confidence=sure()) for region in EdgeRegion
    }


def assessment(**overrides: object) -> ConditionAssessment:
    values: dict[str, object] = {
        "centering": Centering(
            front_horizontal=0.55,
            front_vertical=0.48,
            back_horizontal=0.5,
            back_vertical=0.5,
            confidence=sure(),
        ),
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
# The record's shape
# ----------------------------------------------------------------


def test_the_record_carries_section_13s_tree_and_is_json() -> None:
    """The record's keys are §13's members, and the whole thing serializes.

    `json.dumps` with no default is the family's plain-JSON-types guarantee:
    an enum member, a Confidence or a frozen mapping leaking through would
    raise here rather than in the database driver.
    """
    record = assessment().as_record()

    assert tuple(record) == (
        "centering",
        "corners",
        "edges",
        "surface",
        "manufacturing_defects",
        "eye_appeal",
        "confidence",
    )
    json.dumps(record)


def test_confidences_are_floats_and_vocabulary_is_strings() -> None:
    """`confidence.value`, never `str(Confidence)` — that is the "90%" form."""
    record = assessment().as_record()

    assert record["confidence"] == 0.8
    centering = record["centering"]
    assert isinstance(centering, dict)
    assert centering["confidence"] == 0.9
    assert centering["front_horizontal"] == 0.55
    corners = record["corners"]
    assert isinstance(corners, dict)
    assert set(corners) == {"front", "back"}
    front = corners["front"]
    assert set(front) == {"top_left", "top_right", "bottom_left", "bottom_right"}
    assert front["top_left"] == {"label": "clean", "confidence": 0.9}


def test_every_refusal_serializes_as_the_same_one_key_object() -> None:
    """One rule for the whole tree: a refusal is `{"insufficient_information": reason}`.

    A reader discriminates an answer from a refusal by the key's presence, so
    the shape must be identical at the axis, the ratio and the class level —
    and the reasonless case carries `None` rather than dropping the key.
    """
    record = assessment(
        centering=InsufficientInformation("no_frame_found"),
        corners={
            ImageSide.FRONT: sound_corners(),
            ImageSide.BACK: INSUFFICIENT_INFORMATION,
        },
        manufacturing_defects=InsufficientInformation("manufacturing_classes_not_assessed"),
        eye_appeal=InsufficientInformation("eye_appeal_not_measured_in_v1"),
    ).as_record()

    assert record["centering"] == {"insufficient_information": "no_frame_found"}
    corners = record["corners"]
    assert isinstance(corners, dict)
    assert corners["back"] == {"insufficient_information": None}
    assert record["manufacturing_defects"] == {
        "insufficient_information": "manufacturing_classes_not_assessed"
    }
    assert record["eye_appeal"] == {"insufficient_information": "eye_appeal_not_measured_in_v1"}


def test_a_refused_ratio_is_stored_as_itself_beside_the_measured_ones() -> None:
    """The issue's own words: an axis's refusal is never dropped, never zero."""
    record = assessment(
        centering=Centering(
            front_horizontal=0.55,
            front_vertical=0.48,
            back_horizontal=InsufficientInformation("borderless_axis"),
            back_vertical=0.5,
            confidence=sure(),
        )
    ).as_record()

    centering = record["centering"]
    assert isinstance(centering, dict)
    assert centering["back_horizontal"] == {"insufficient_information": "borderless_axis"}
    assert centering["back_vertical"] == 0.5


def test_a_defect_record_spells_section_17_and_omits_what_is_absent() -> None:
    """Omit-not-null, the family convention: an absent optional is no key.

    A minimal defect carries exactly the five required fields; a full one
    carries the box, the polygon and the metadata bag as plain JSON.
    """
    minimal = a_stain()
    full = a_stain(
        bounding_box=BoundingBox(x=0.1, y=0.2, width=0.3, height=0.4),
        polygon=((0.1, 0.2), (0.4, 0.2), (0.4, 0.6)),
        metadata={"area_mm2": 2.5},
    )
    surface = assessment(
        surface={
            ImageSide.FRONT: SurfaceAssessment(findings=(minimal, full)),
            ImageSide.BACK: SurfaceAssessment(findings=()),
        }
    ).as_record()["surface"]

    assert isinstance(surface, dict)
    findings = surface["front"]["findings"]
    assert findings[0] == {
        "type": "stain",
        "confidence": 0.9,
        "severity": "minor",
        "side": "front",
        "representation": "normalized",
    }
    assert findings[1] == {
        "type": "stain",
        "confidence": 0.9,
        "severity": "minor",
        "side": "front",
        "representation": "normalized",
        "bounding_box": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        "polygon": [[0.1, 0.2], [0.4, 0.2], [0.4, 0.6]],
        "metadata": {"area_mm2": 2.5},
    }
    assert surface["back"] == {"findings": [], "not_assessed": {}}


def test_a_class_level_refusal_travels_under_its_label() -> None:
    """#185's `not_assessed` is stored per class, each wearing its reason."""
    record = assessment(
        surface={
            ImageSide.FRONT: SurfaceAssessment(
                findings=(),
                not_assessed={SurfaceLabel.SCRATCH: InsufficientInformation("adr_0010")},
            ),
            ImageSide.BACK: SurfaceAssessment(findings=()),
        }
    ).as_record()

    surface = record["surface"]
    assert isinstance(surface, dict)
    assert surface["front"]["not_assessed"] == {"scratch": {"insufficient_information": "adr_0010"}}


def test_a_finding_with_a_severity_and_a_box_carries_both() -> None:
    """The corner/edge record: label, confidence, then omit-not-null extras."""
    worn = RegionFinding(
        label=EdgeLabel.WHITENING,
        confidence=Confidence(0.7),
        severity=DefectSeverity.MODERATE,
        bounding_box=BoundingBox(x=0.0, y=0.0, width=0.5, height=0.01),
    )
    edges = dict(sound_edges())
    edges[EdgeRegion.TOP] = worn
    record = assessment(edges={ImageSide.FRONT: edges, ImageSide.BACK: sound_edges()}).as_record()

    top = record["edges"]["front"]["top"]
    assert top == {
        "label": "whitening",
        "confidence": 0.7,
        "severity": "moderate",
        "bounding_box": {"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.01},
    }


def test_manufacturing_defects_serialize_as_a_list_of_defects() -> None:
    record = assessment(manufacturing_defects=(a_stain(type=SurfaceLabel.PRINT_LINE),)).as_record()

    assert record["manufacturing_defects"] == [
        {
            "type": "print_line",
            "confidence": 0.9,
            "severity": "minor",
            "side": "front",
            "representation": "normalized",
        }
    ]


# ----------------------------------------------------------------
# card_frame_of — the deriver, lifted from the datasets domain
# ----------------------------------------------------------------


def test_the_card_frame_comes_from_the_stored_record() -> None:
    """#182's rule: the frame is derived from `normalization_details`, never
    from the normalizer's current thresholds — a #194 artifact's card sits
    inside the stored margin."""
    frame = card_frame_of(
        {
            "width": 804,
            "height": 1104,
            "thresholds": {
                "normalization_margin_mm": 2.0,
                "normalization_pixels_per_mm": 12.0,
            },
        }
    )

    assert frame == BoundingBox(x=24 / 804, y=24 / 1104, width=756 / 804, height=1056 / 1104)


def test_a_record_with_no_margin_keys_is_a_pre_194_artifact() -> None:
    """Its card really does reach the edges: the frame is the unit square."""
    assert card_frame_of({"width": 756, "height": 1056}) == BoundingBox(
        x=0.0, y=0.0, width=1.0, height=1.0
    )


def test_no_record_or_no_dimensions_derives_no_frame() -> None:
    assert card_frame_of(None) is None
    assert card_frame_of({}) is None
    assert card_frame_of({"width": 804}) is None
    assert card_frame_of({"width": "804", "height": "1104"}) is None
    # A zero or negative dimension is a corrupt record, and the answer is the
    # refusal path — never a ZeroDivisionError out of a worker job.
    assert card_frame_of({"width": 0, "height": 1104}) is None
    assert card_frame_of({"width": 804, "height": -1104}) is None
    assert card_frame_of({"width": True, "height": 1104}) is None


def test_a_margin_that_leaves_no_card_derives_no_frame() -> None:
    """A corrupt record refuses rather than yielding a degenerate box —
    `BoundingBox` validates where the datasets' wire shape did not."""
    assert (
        card_frame_of(
            {
                "width": 100,
                "height": 100,
                "thresholds": {
                    "normalization_margin_mm": 50.0,
                    "normalization_pixels_per_mm": 1.0,
                },
            }
        )
        is None
    )
