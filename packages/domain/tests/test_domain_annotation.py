"""Unit tests for spec §30's annotation vocabularies.

Transcriptions of closed lists in the specification, so the tests are
transcriptions too: the values are written out again here rather than derived
from the enums, because a test that reads its expectation from the code under
test proves only that the code equals itself.

Two of the assertions below are about what the specification does *not* say, and
they are the ones most likely to be "fixed" by a later reader:

* §16's surface labels contain no `clean`, where §14's corners and §15's edges
  both do. A clean surface is the absence of surface annotations; a clean corner
  is a corner annotation saying so.
* §14 lists its corners front- and back-prefixed. The prefix is
  `training_images.side`, and repeating it here would let a row disagree with the
  image it annotates.
"""

from __future__ import annotations

import pytest
from tcg_domain.annotation import (
    LABELS_BY_KIND,
    NO_DEFECT_LABELS,
    REGIONS_BY_KIND,
    AnnotationKind,
    CornerLabel,
    CornerRegion,
    DefectSeverity,
    EdgeLabel,
    EdgeRegion,
    SurfaceLabel,
)

# Spec §14, with the front_/back_ prefix removed — see the module docstring.
SECTION_14_CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")

# Spec §14's potential labels, in the order the specification lists them.
SECTION_14_LABELS = (
    "clean",
    "whitening",
    "rounding",
    "chipping",
    "dent",
    "crease",
    "layering",
    "unknown",
)

# Spec §15's potential labels. Note `rough_cut` and `notching`, which §14 has
# not, and no `rounding`, which it has — the two lists are not the same list.
SECTION_15_LABELS = (
    "clean",
    "whitening",
    "chipping",
    "rough_cut",
    "notching",
    "layering",
    "dent",
    "unknown",
)

# Spec §16's potential classes.
SECTION_16_LABELS = (
    "scratch",
    "print_line",
    "dent",
    "indentation",
    "stain",
    "scuff",
    "print_dot",
    "color_issue",
    "registration_issue",
    "gloss_issue",
    "factory_defect",
    "unknown",
)


def test_the_three_kinds_are_section_30s_three_annotation_features() -> None:
    """§30 lists corner, edge and surface defect annotation.

    Centering is §30's fourth, and it is a measurement rather than a marker —
    `centering_measurements` is its own table and is deliberately not a fourth
    member here.
    """
    assert tuple(kind.value for kind in AnnotationKind) == ("corner", "edge", "surface")


def test_the_four_corners_carry_no_side_prefix() -> None:
    """§14's eight are four positions on each of two sides.

    Which side is `training_images.side`; naming it twice would let an annotation
    claim a side its image does not have.
    """
    assert tuple(region.value for region in CornerRegion) == SECTION_14_CORNERS


def test_the_four_edges_are_the_four_edges() -> None:
    """§15 says to represent front and back separately, by the same means."""
    assert tuple(region.value for region in EdgeRegion) == ("top", "right", "bottom", "left")


def test_the_corner_labels_are_section_14s() -> None:
    assert tuple(label.value for label in CornerLabel) == SECTION_14_LABELS


def test_the_edge_labels_are_section_15s() -> None:
    assert tuple(label.value for label in EdgeLabel) == SECTION_15_LABELS


def test_the_surface_labels_are_section_16s() -> None:
    assert tuple(label.value for label in SurfaceLabel) == SECTION_16_LABELS


def test_a_surface_has_no_clean_label_and_a_corner_does() -> None:
    """The asymmetry is the specification's, and it is load-bearing.

    §16's twelve classes are all defects: a surface with nothing wrong is a
    surface nobody annotated. §14 and §15 both open with `clean`, so a corner
    inspected and found sound is recorded rather than inferred from silence.
    """
    assert "clean" not in {label.value for label in SurfaceLabel}
    assert "clean" in {label.value for label in CornerLabel}
    assert "clean" in {label.value for label in EdgeLabel}


def test_every_vocabulary_admits_unknown() -> None:
    """Spec §2.7's uncertainty, in the vocabulary rather than beside it.

    An annotator who can see damage but cannot name it says `unknown` and still
    records where it is; that is a usable training signal, and a forced guess is
    not.
    """
    for vocabulary in (CornerLabel, EdgeLabel, SurfaceLabel):
        assert "unknown" in {label.value for label in vocabulary}


def test_the_three_severities_are_ordinal_and_not_a_scale() -> None:
    """§17 requires a severity and defines none.

    Three levels a person can reproduce, rather than a `[0, 1]` number they
    cannot: there is one annotator and no agreement study to calibrate finer
    granularity against, so the extra precision would be noise a model fits.
    """
    assert tuple(level.value for level in DefectSeverity) == ("minor", "moderate", "severe")


def test_the_labels_are_total_over_the_kinds() -> None:
    """The CHECK constraint is composed from this, so a gap here is a gap there."""
    assert set(LABELS_BY_KIND) == set(AnnotationKind)
    assert LABELS_BY_KIND[AnnotationKind.CORNER] == frozenset(SECTION_14_LABELS)
    assert LABELS_BY_KIND[AnnotationKind.EDGE] == frozenset(SECTION_15_LABELS)
    assert LABELS_BY_KIND[AnnotationKind.SURFACE] == frozenset(SECTION_16_LABELS)


def test_a_surface_annotation_has_no_region() -> None:
    """§16 names no positions; a surface defect's position is its bounding box.

    Empty rather than absent, so the mapping stays total and the constraint that
    reads it needs no special case.
    """
    assert set(REGIONS_BY_KIND) == set(AnnotationKind)
    assert REGIONS_BY_KIND[AnnotationKind.SURFACE] == frozenset()
    assert REGIONS_BY_KIND[AnnotationKind.CORNER] == frozenset(SECTION_14_CORNERS)
    assert REGIONS_BY_KIND[AnnotationKind.EDGE] == frozenset(region.value for region in EdgeRegion)


def test_the_labels_that_assert_no_defect_are_the_two_that_carry_no_severity() -> None:
    """`clean` found nothing to rate and `unknown` could not rate what it found.

    Every other label names a defect, and §17 requires a severity beside one —
    which is what `ck_annotations_a_defect_carries_a_severity` enforces.
    """
    assert frozenset({"clean", "unknown"}) == NO_DEFECT_LABELS
    for labels in LABELS_BY_KIND.values():
        assert NO_DEFECT_LABELS & labels


@pytest.mark.parametrize(
    "vocabulary",
    [
        AnnotationKind,
        CornerRegion,
        EdgeRegion,
        CornerLabel,
        EdgeLabel,
        SurfaceLabel,
        DefectSeverity,
    ],
    ids=["kind", "corner", "edge", "corner-label", "edge-label", "surface-label", "severity"],
)
def test_members_are_strings(vocabulary: type[AnnotationKind]) -> None:
    """`StrEnum`, so nothing has to remember `.value` before a query or a CHECK."""
    for member in vocabulary:
        assert isinstance(member, str)
        assert member == member.value
