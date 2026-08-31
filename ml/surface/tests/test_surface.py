"""Surface classification — spec §16/§17, issue #185.

Needs no database, no object storage and no network, so every claim here is
asserted on every push. The artifacts are **built in the test**, on the rule
`ml/edges/tests/test_edges.py` restates: a binary blob in the repository is
one nobody can read at review time, and a test asserting that a 48-pixel
square is a moderate stain is only convincing if the reader can watch the
square being drawn.

The card frame is constructed by hand rather than read from a normalization
record — this package does not depend on `ml/normalization`; the frame
crosses between them as a domain type.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from tcg_domain.analysis import ImageSide
from tcg_domain.annotation import DefectSeverity, SurfaceLabel
from tcg_domain.condition import BoundingBox, Representation, SurfaceAssessment
from tcg_domain.confidence import InsufficientInformation
from tcg_ml_surface import (
    DEFAULT_SURFACE_THRESHOLDS,
    SURFACE_VERSION,
    SurfaceThresholds,
    classify,
)

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

#: A plain, slightly warm face (BGR): mid-tone, so it is neither the stain
#: mask's dark nor the scuff mask's near-white, and perfectly flat, so the
#: busyness map reads zero.
FACE_BGR = (140, 150, 160)

#: The photographed surface in the margin — grey, like the real background.
SURFACE_BGR = (90, 90, 90)

#: A dark foreign mark (ink, grime) and a dull whitish abrasion.
STAIN_BGR = (30, 30, 30)
SCUFF_BGR = (245, 245, 245)

#: The border strip surface never reads: the edge and corner analyzers'
#: detection and reference bands (edges' inset + two bands = 26 px).
BORDER_EXCLUSION = 26

#: The four classes ADR 0010 puts below the artifact's sampling limit.
FINE_CLASSES = (
    SurfaceLabel.SCRATCH,
    SurfaceLabel.PRINT_LINE,
    SurfaceLabel.PRINT_DOT,
    SurfaceLabel.GLOSS_ISSUE,
)

#: What v0.1.0 refuses on every side, foil or not: the fine four plus the
#: five coarse classes the baseline has no signal for.
ALWAYS_REFUSED = (
    *FINE_CLASSES,
    SurfaceLabel.DENT,
    SurfaceLabel.INDENTATION,
    SurfaceLabel.COLOR_ISSUE,
    SurfaceLabel.REGISTRATION_ISSUE,
    SurfaceLabel.FACTORY_DEFECT,
)


def a_plain_card() -> NDArray[np.uint8]:
    """An artifact: grey margin, one flat mid-tone card face."""
    picture = np.empty((HEIGHT, WIDTH, 3), np.uint8)
    picture[:, :] = SURFACE_BGR
    the_card(picture)[:, :] = FACE_BGR
    return picture


def the_card(picture: NDArray[np.uint8]) -> NDArray[np.uint8]:
    return picture[MARGIN_PX : MARGIN_PX + CARD_PX_HEIGHT, MARGIN_PX : MARGIN_PX + CARD_PX_WIDTH]


def a_blob(
    picture: NDArray[np.uint8],
    color: tuple[int, int, int],
    *,
    at: tuple[int, int],
    size: tuple[int, int],
) -> None:
    """Paint one solid rectangle on the card, in card pixels (row, column)."""
    row, column = at
    rows, columns = size
    the_card(picture)[row : row + rows, column : column + columns] = color


def busy_texture(picture: NDArray[np.uint8], *, at: tuple[int, int], size: tuple[int, int]) -> None:
    """Fill a card region with fine grey noise: never dark enough for the
    stain mask, never bright enough for the scuff mask, but busy at every
    pixel — artwork and foil as the busyness map sees them."""
    rng = np.random.default_rng(11)
    row, column = at
    rows, columns = size
    grey = rng.integers(80, 180, (rows, columns), np.uint8)
    the_card(picture)[row : row + rows, column : column + columns] = np.stack([grey] * 3, axis=2)


def encoded(picture: NDArray[np.uint8]) -> bytes:
    ok, data = cv2.imencode(".png", picture)
    assert ok
    return bytes(data.tobytes())


def classified(
    picture: NDArray[np.uint8], *, side: ImageSide = ImageSide.FRONT
) -> SurfaceAssessment:
    result = classify(encoded(picture), side=side, card_frame=CARD_FRAME)
    assert not isinstance(result, InsufficientInformation)
    return result


def test_a_plain_card_reports_a_clean_surface() -> None:
    """A clean face is an empty findings tuple — `SurfaceLabel` has no
    `clean` (the asymmetry documented in `tcg_domain.annotation`) — and the
    nine classes the baseline never claims are refused class-level, never
    silently omitted."""
    assessment = classified(a_plain_card())

    assert assessment.findings == ()
    assert set(assessment.not_assessed) == set(ALWAYS_REFUSED)
    for refusal in assessment.not_assessed.values():
        assert isinstance(refusal, InsufficientInformation)
        assert refusal.reason


def test_the_fine_classes_are_refused_with_adr_0010s_reason() -> None:
    """§16 requires the fine vocabulary and ADR 0010 is the recorded reason
    it answers `insufficient_information` against the artifact."""
    assessment = classified(a_plain_card())

    for label in FINE_CLASSES:
        reason = assessment.not_assessed[label].reason
        assert reason is not None
        assert "ADR 0010" in reason


def test_a_dark_blob_is_a_stain() -> None:
    picture = a_plain_card()
    a_blob(picture, STAIN_BGR, at=(200, 300), size=(48, 48))

    assessment = classified(picture)

    (finding,) = assessment.findings
    assert finding.type is SurfaceLabel.STAIN
    assert finding.side is ImageSide.FRONT
    assert finding.representation is Representation.NORMALIZED
    assert finding.severity is not None
    assert finding.polygon is None


def test_a_dull_white_patch_is_a_scuff() -> None:
    picture = a_plain_card()
    a_blob(picture, SCUFF_BGR, at=(400, 200), size=(48, 48))

    assessment = classified(picture)

    (finding,) = assessment.findings
    assert finding.type is SurfaceLabel.SCUFF


def test_the_bounding_box_names_where_the_stain_sits() -> None:
    """The finding's spatial claim is fractions of the whole artifact (§17):
    the card origin places the drawn rectangle exactly."""
    picture = a_plain_card()
    a_blob(picture, STAIN_BGR, at=(200, 300), size=(48, 64))

    (finding,) = classified(picture).findings

    box = finding.bounding_box
    assert isinstance(box, BoundingBox)
    assert box.x == pytest.approx((MARGIN_PX + 300) / WIDTH, abs=1e-9)
    assert box.y == pytest.approx((MARGIN_PX + 200) / HEIGHT, abs=1e-9)
    assert box.width == pytest.approx(64 / WIDTH, abs=1e-9)
    assert box.height == pytest.approx(48 / HEIGHT, abs=1e-9)


@pytest.mark.parametrize(
    ("size", "severity"),
    [
        # 24 x 24 px = 4.0 mm² at 144 px²/mm² — past the 1.0 mm² floor,
        # under the 10.0 mm² moderate boundary.
        ((24, 24), DefectSeverity.MINOR),
        # 48 x 48 px = 16.0 mm² — between 10.0 and 25.0.
        ((48, 48), DefectSeverity.MODERATE),
        # 72 x 72 px = 36.0 mm² — past 25.0.
        ((72, 72), DefectSeverity.SEVERE),
    ],
)
def test_severity_is_banded_on_area(size: tuple[int, int], severity: DefectSeverity) -> None:
    picture = a_plain_card()
    a_blob(picture, STAIN_BGR, at=(200, 300), size=size)

    (finding,) = classified(picture).findings

    assert finding.severity is severity


def test_a_speck_below_the_area_floor_is_not_reported() -> None:
    """8 x 8 px is 0.44 mm² — coarse classes are millimetre-scale, and a
    sub-millimetre speck is the fine classes' territory, which is refused."""
    picture = a_plain_card()
    a_blob(picture, STAIN_BGR, at=(200, 300), size=(8, 8))

    assert classified(picture).findings == ()


def test_a_blob_inside_busy_artwork_is_not_reported() -> None:
    """The per-candidate context gate (#176's filter-before-selection): a
    dark patch surrounded by busy texture is artwork, not a stain — the
    false positives this drops and the true stains it hides are #188's to
    price."""
    picture = a_plain_card()
    busy_texture(picture, at=(150, 250), size=(150, 150))
    a_blob(picture, STAIN_BGR, at=(200, 300), size=(48, 48))

    assessment = classified(picture)

    assert assessment.findings == ()
    assert SurfaceLabel.STAIN not in assessment.not_assessed


def test_a_busy_face_refuses_stain_and_scuff_class_level() -> None:
    """The issue's holo clause: foil texture reads as defect texture to this
    baseline, so on a busy face `stain` and `scuff` join the refused classes
    — a class-level verdict, not a whole-side refusal and not a guess."""
    picture = a_plain_card()
    busy_texture(picture, at=(0, 0), size=(CARD_PX_HEIGHT, CARD_PX_WIDTH))

    assessment = classified(picture)

    assert assessment.findings == ()
    assert set(assessment.not_assessed) == set(SurfaceLabel) - {SurfaceLabel.UNKNOWN}
    for label in (SurfaceLabel.STAIN, SurfaceLabel.SCUFF):
        reason = assessment.not_assessed[label].reason
        assert reason is not None
        assert "texture" in reason


def test_a_patch_in_the_border_strip_belongs_to_the_edge_axes() -> None:
    """The axis seam: the outer 26 px are the edge and corner analyzers'
    detection and reference bands, and near-white there is their whitening
    signal — reporting it here too would double-report one defect across
    two axes (#184's rule, extended)."""
    picture = a_plain_card()
    a_blob(picture, SCUFF_BGR, at=(2, 200), size=(12, 100))

    assert classified(picture).findings == ()


def test_both_polarities_report_together() -> None:
    picture = a_plain_card()
    a_blob(picture, STAIN_BGR, at=(200, 300), size=(48, 48))
    a_blob(picture, SCUFF_BGR, at=(500, 200), size=(48, 48))

    findings = classified(picture).findings

    assert {finding.type for finding in findings} == {SurfaceLabel.STAIN, SurfaceLabel.SCUFF}
    assert {finding.side for finding in findings} == {ImageSide.FRONT}


def test_the_side_is_the_callers() -> None:
    """`Defect` carries a side and `ConditionAssessment.surface` checks it
    against its key — the caller names which side's artifact this is."""
    picture = a_plain_card()
    a_blob(picture, STAIN_BGR, at=(200, 300), size=(48, 48))

    (finding,) = classified(picture, side=ImageSide.BACK).findings

    assert finding.side is ImageSide.BACK


def test_confidence_is_a_bounded_heuristic() -> None:
    """Margin-from-threshold in [0.5, 0.95] — a heuristic, not a calibrated
    probability; §26's calibration is M8's."""
    picture = a_plain_card()
    a_blob(picture, STAIN_BGR, at=(200, 300), size=(48, 48))

    (finding,) = classified(picture).findings

    assert 0.5 <= finding.confidence.value <= 0.95


def test_confidence_grows_with_the_area_margin() -> None:
    """The margin recipe is not a constant: more area past the floor is more
    confidence. Both blobs sit inside the recipe's 1.0-2.0 mm² window —
    the margin saturates at twice the floor, so anything past 288 px would
    compare equal at 0.95."""
    smaller = a_plain_card()
    a_blob(smaller, STAIN_BGR, at=(200, 300), size=(13, 13))
    larger = a_plain_card()
    a_blob(larger, STAIN_BGR, at=(200, 300), size=(16, 16))

    (faint,) = classified(smaller).findings
    (plain,) = classified(larger).findings

    assert faint.confidence.value < plain.confidence.value


def test_undecodable_bytes_refuse_the_whole_side() -> None:
    result = classify(b"not a png", side=ImageSide.FRONT, card_frame=CARD_FRAME)

    assert isinstance(result, InsufficientInformation)
    assert result.reason is not None
    assert "decoded" in result.reason


def test_a_card_frame_too_small_refuses_the_whole_side() -> None:
    small = BoundingBox(x=0.4, y=0.4, width=0.05, height=0.05)

    result = classify(encoded(a_plain_card()), side=ImageSide.FRONT, card_frame=small)

    assert isinstance(result, InsufficientInformation)
    assert result.reason is not None
    assert "too small" in result.reason


def test_a_pre_194_artifact_with_no_margin_still_classifies() -> None:
    """A pre-#194 artifact has no margin: the caller says so with the whole
    unit square."""
    bare = the_card(a_plain_card())

    result = classify(
        encoded(np.ascontiguousarray(bare)),
        side=ImageSide.FRONT,
        card_frame=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
    )

    assert not isinstance(result, InsufficientInformation)
    assert result.findings == ()


def test_thresholds_reject_disorder() -> None:
    with pytest.raises(ValueError):
        SurfaceThresholds(severe_min_area_mm2=5.0)
    with pytest.raises(ValueError):
        SurfaceThresholds(face_busy_fraction=1.5)
    with pytest.raises(ValueError):
        SurfaceThresholds(border_exclusion_px=0)
    with pytest.raises(ValueError):
        SurfaceThresholds(context_margin_px=0)
    with pytest.raises(ValueError):
        SurfaceThresholds(laplacian_threshold=0)


def test_thresholds_serialise_with_the_surface_prefix() -> None:
    record = DEFAULT_SURFACE_THRESHOLDS.as_record()

    assert record
    assert all(key.startswith("surface_") for key in record)
    assert all(isinstance(value, float) for value in record.values())


def test_the_version_names_this_classification() -> None:
    """The pin: changing any threshold's value means bumping this constant."""
    assert SURFACE_VERSION == "surface-opencv-v0.1.0"
