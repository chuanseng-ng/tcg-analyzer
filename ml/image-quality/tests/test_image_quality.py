"""The image-quality gate's heuristics — spec §19, issue #36.

Needs no database, no object storage and no network, so every claim here is
asserted on every push. The photographs are **built in the test**, on the rule
`services/api/tests/test_image_validation.py` states: a binary blob in the
repository is one nobody can read at review time, and a test asserting that a
blurred photograph is refused is only convincing if the reader can watch it
being blurred.

Built with OpenCV rather than Pillow, so this package's tests need nothing this
package does not already depend on. Encoded as PNG unless the point is the JPEG
decoder: JPEG is lossy, and a test about blur should not have a quantiser adding
some of its own.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from tcg_domain.analysis import QualityStatus
from tcg_domain.card_geometry import CardGeometry
from tcg_domain.confidence import INSUFFICIENT_INFORMATION, Confidence, InsufficientInformation
from tcg_domain.image_quality import (
    NEEDS_CARD_GEOMETRY,
    ConditionVerdict,
    QualityCondition,
)
from tcg_ml_image_quality import (
    DEFAULT_THRESHOLDS,
    IMAGE_QUALITY_VERSION,
    QualityThresholds,
    UnreadableImage,
    assess,
)

#: Portrait, and comfortably above the resolution floor, so that a fixture only
#: fails the condition it is built to fail.
SIZE = (1600, 1200)  # height, width


def a_photograph(rng_seed: int = 0) -> NDArray[np.uint8]:
    """A sharp, well-exposed picture with structure at more than one scale.

    Blocks rather than per-pixel noise, and that is not cosmetic: the gate
    measures a copy scaled down to `work_long_edge`, and `INTER_AREA` averages
    independent noise almost away — a fixture made of it arrives at the
    heuristics as a flat grey and is reported, correctly, as having no range and
    no detail. Blocks survive the downscale, which is what a photograph of a card
    does: borders, text and frame edges are the structure that is still there at
    a thousand pixels across.

    Both ends of the histogram are used and neither is reached, so it trips
    neither the exposure heuristics nor the glare one.
    """
    rng = np.random.default_rng(rng_seed)
    rows, columns = np.mgrid[0 : SIZE[0], 0 : SIZE[1]]
    blocks = (((rows // 16) + (columns // 16)) % 2) * 195 + 20
    speckle = rng.integers(-18, 19, size=SIZE)
    gray = np.clip(blocks + speckle, 0, 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def png(image: NDArray[np.uint8]) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    assert ok, "the fixture could not be encoded"
    return bytes(buffer)


def jpeg(image: NDArray[np.uint8]) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok, "the fixture could not be encoded"
    return bytes(buffer)


def verdict(
    data: bytes, condition: QualityCondition, geometry: CardGeometry | None = None
) -> ConditionVerdict:
    return assess(data, geometry=geometry).of(condition).verdict


def severity(
    data: bytes, condition: QualityCondition, geometry: CardGeometry | None = None
) -> QualityStatus | None:
    return assess(data, geometry=geometry).of(condition).severity


# ---------------------------------------------------------------------------
# A photograph with nothing wrong with it
# ---------------------------------------------------------------------------


def test_an_ordinary_photograph_trips_none_of_the_five() -> None:
    report = assess(png(a_photograph()))
    tripped = [
        str(finding.condition)
        for finding in report.findings
        if finding.verdict is ConditionVerdict.DETECTED
    ]

    assert tripped == []


def test_a_jpeg_is_read_as_readily_as_a_png() -> None:
    """The stored bytes are whichever of the two the upload accepted."""
    report = assess(jpeg(a_photograph()))

    assert report.status is not QualityStatus.UNUSABLE


def test_bytes_that_do_not_decode_are_a_job_failure_rather_than_a_verdict() -> None:
    """The upload already decoded these bytes, so this means the object is wrong."""
    with pytest.raises(UnreadableImage):
        assess(b"this is not an image")


# ---------------------------------------------------------------------------
# The five conditions the frame alone decides
# ---------------------------------------------------------------------------


def test_a_blurred_photograph_is_detected() -> None:
    blurred = cv2.GaussianBlur(a_photograph(), (0, 0), sigmaX=8)

    assert verdict(png(blurred), QualityCondition.BLUR) is ConditionVerdict.DETECTED


def test_a_badly_blurred_photograph_is_unusable() -> None:
    """Nothing downstream can measure a corner in this, so the analysis stops."""
    blurred = cv2.GaussianBlur(a_photograph(), (0, 0), sigmaX=25)

    assert assess(png(blurred)).status is QualityStatus.UNUSABLE


def test_a_small_photograph_is_detected_as_low_resolution() -> None:
    small = cv2.resize(a_photograph(), (450, 600), interpolation=cv2.INTER_AREA)

    assert verdict(png(small), QualityCondition.LOW_RESOLUTION) is ConditionVerdict.DETECTED


def test_low_resolution_is_measured_on_the_original_not_the_working_copy() -> None:
    """The gate scales down to measure everything else; this one must not follow.

    Otherwise every photograph would be exactly `work_long_edge` and the
    condition could never fire.
    """
    big = a_photograph()

    assert verdict(png(big), QualityCondition.LOW_RESOLUTION) is ConditionVerdict.CLEAR
    assert assess(png(big)).of(QualityCondition.LOW_RESOLUTION).measurement == min(SIZE)


def test_a_dark_photograph_is_detected() -> None:
    dark = (a_photograph() // 5).astype(np.uint8)

    assert verdict(png(dark), QualityCondition.EXCESSIVE_DARKNESS) is ConditionVerdict.DETECTED


def test_a_blown_out_photograph_is_detected_as_too_bright() -> None:
    bright = (255 - a_photograph() // 6).astype(np.uint8)

    assert verdict(png(bright), QualityCondition.EXCESSIVE_BRIGHTNESS) is ConditionVerdict.DETECTED


def test_a_flat_histogram_is_poor_exposure() -> None:
    """A mid-tone photograph with almost no range — hazy light, or a lost fight
    with a backlit scene. Neither dark nor bright, and not something any later
    stage can measure a defect in."""
    rng = np.random.default_rng(1)
    flat = rng.integers(120, 135, size=(*SIZE, 3), dtype=np.uint8)

    assert verdict(png(flat), QualityCondition.POOR_EXPOSURE) is ConditionVerdict.DETECTED


# ---------------------------------------------------------------------------
# The six that need the card located — issues #37 and #208
#
# The geometries here are built by hand rather than detected, because what is
# under test is the *judging*: `ml/card-detection` owns whether a quadrilateral
# is the right one, and asserting both at once would leave neither pinned.
# ---------------------------------------------------------------------------

DETECTOR = "card-detection-test-v0"


def a_card(**overrides: object) -> CardGeometry:
    """A card square-on, filling a comfortable share of `SIZE`."""
    height, width = SIZE
    fields: dict[str, object] = {
        "corners": (
            (0.2 * width, 0.15 * height),
            (0.8 * width, 0.15 * height),
            (0.8 * width, 0.85 * height),
            (0.2 * width, 0.85 * height),
        ),
        "confidence": Confidence.of(0.95),
        "frame_width": width,
        "frame_height": height,
        "detector": DETECTOR,
    }
    fields.update(overrides)
    return CardGeometry(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Glare — issue #208
#
# Sized against the region glare is actually measured over, which is neither
# the frame nor the whole card: the quadrilateral above, scaled into the
# working copy and eroded on every side. A fixture sized against the frame
# lands in the wrong band the moment a threshold moves, which is #176's rule
# — a fixture whose proportions are not the real ones asserts nothing.
# ---------------------------------------------------------------------------


def measured_card_area() -> float:
    """The card region glare is measured over, in the working copy's pixels."""
    scale = DEFAULT_THRESHOLDS.work_long_edge / max(SIZE)
    long_edge = 0.70 * SIZE[0] * scale
    short_edge = 0.60 * SIZE[1] * scale
    inset = 2 * round(DEFAULT_THRESHOLDS.glare_card_inset_fraction * long_edge)
    return (long_edge - inset) * (short_edge - inset)


def radius_for(fraction: float) -> int:
    """The radius, in `SIZE`'s pixels, of a disc covering `fraction` of that region."""
    scale = DEFAULT_THRESHOLDS.work_long_edge / max(SIZE)
    return round(float(np.sqrt(fraction * measured_card_area() / np.pi)) / scale)


def a_colour_photograph(rng_seed: int = 0) -> NDArray[np.uint8]:
    """`a_photograph`, printed in colour.

    Glare is measured off the chroma, and `a_photograph` has none: it is a
    grayscale picture widened to three channels, so both of its CIELAB chroma
    channels are flat and any chroma statistic taken against it is a statistic
    about nothing. A card is printed in colour, and the fixtures that exercise
    glare have to be. The luminance is `a_photograph`'s unchanged, so the other
    four conditions read exactly what they read there.
    """
    gray = cv2.cvtColor(a_photograph(rng_seed), cv2.COLOR_BGR2GRAY)
    rows, columns = np.mgrid[0 : SIZE[0], 0 : SIZE[1]]
    hue = (((rows // 64) * 5 + (columns // 64) * 11) % 180).astype(np.uint8)
    hsv = np.stack([hue, np.full(SIZE, 170, dtype=np.uint8), gray], axis=-1)
    coloured: NDArray[np.uint8] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return coloured


#: A sheened card, in two parts, because real ones arrive in two parts — see
#: `with_sheen`. `VEIL` is how much of the print's colour the wash takes away
#: across the whole face; `BAND_THICKNESS` is how much of the frame's height the
#: dispersed band covers, and `BAND_CYCLES` how many times its hue goes round.
#: Together they put the fixture where the corpus's reflecting photographs
#: measured: a chroma-gradient median of 1.9 against their 1.3-1.6, and a
#: reflecting fraction of 0.022 against their 0.010-0.042.
VEIL = 0.55
BAND_THICKNESS = 0.10
BAND_CYCLES = 2


def with_sheen(picture: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Holo sheen laid over `picture`: a washed face and a dispersed band.

    Not a disc of white, and not one thing. Light returned off a foil layer
    arrives two ways at once, and the corpus shows both: the whole face is
    partly washed, which takes the colour out of the print underneath, and a
    band where the grating disperses the return into spectral orders lands as a
    smooth ramp of hue running *across* the card rather than with the artwork.
    Feathered rather than hard-edged, because a hard bright edge is the thing
    the old measurement could already see.

    The washed face is not decoration. It is what makes a reflecting card
    measurably different from a busy one: it lowers the card's own median
    colour-change while the band raises the peak, and the measurement is the
    ratio between them.
    """
    rows, columns = np.mgrid[0 : SIZE[0], 0 : SIZE[1]]
    band = np.exp(-(((rows - 0.38 * SIZE[0]) / (0.5 * BAND_THICKNESS * SIZE[0])) ** 2))
    hsv = cv2.cvtColor(picture, cv2.COLOR_BGR2HSV).astype(np.float32)

    hsv[:, :, 1] *= 1.0 - VEIL
    hsv[:, :, 2] = np.minimum(hsv[:, :, 2] + VEIL * 40.0, 235.0)

    ramp = (columns / SIZE[1]) * 179.0 * BAND_CYCLES % 180.0
    hsv[:, :, 0] = (1.0 - band) * hsv[:, :, 0] + band * ramp
    hsv[:, :, 1] = (1.0 - band) * hsv[:, :, 1] + band * 95.0
    hsv[:, :, 2] = np.minimum((1.0 - band) * hsv[:, :, 2] + band * 235.0, 235.0)

    sheened: NDArray[np.uint8] = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    return sheened


def test_a_band_of_diffuse_sheen_is_glare() -> None:
    """The measurement #208 exists for, and the one v0.2.0 could not make.

    The assertion on the maximum is the point of the fixture: nothing in it
    comes near the blown-out level, so a gate counting only saturated pixels
    reports this photograph perfectly clean — which is what #190 measured it
    doing on all 28 real ones.

    `poor` and not `unusable`, deliberately: the annotator worked with every
    reflecting photograph in the corpus, so this is a warning.
    """
    sheened = with_sheen(a_colour_photograph())

    assert sheened.max() < DEFAULT_THRESHOLDS.glare_level
    assert severity(png(sheened), QualityCondition.GLARE, a_card()) is QualityStatus.POOR


def test_an_ordinary_colour_photograph_has_no_glare() -> None:
    """The same fixture without the band, so the band is what was detected."""
    assert (
        verdict(png(a_colour_photograph()), QualityCondition.GLARE, a_card())
        is ConditionVerdict.CLEAR
    )


def test_a_photograph_with_no_colour_in_it_has_no_glare() -> None:
    """The measure is a ratio, so it needs no floor under a colourless picture.

    `a_photograph` is grayscale widened to three channels. Through a lossy
    encoder its chroma is noise, and noise raises the card's median exactly as
    much as it raises everything else — which is the property that lets the
    threshold be relative at all.
    """
    assert verdict(jpeg(a_photograph()), QualityCondition.GLARE, a_card()) is ConditionVerdict.CLEAR


def test_a_large_flat_bright_panel_is_not_glare() -> None:
    """A card's own text box is bright, flat and large, and is printed there.

    The obvious false positive, and the one the corpus warned about: measured
    over the whole card, the photographs that scored highest on brightness and
    on flatness were the *backs* — a big even printed field. What separates a
    reflection from print is that the reflection changes the card's colour and
    print does not, which is why the measurement is made on the chroma.
    """
    panelled = a_colour_photograph()
    panelled[500:1100, 350:850] = (238, 238, 238)

    assert verdict(png(panelled), QualityCondition.GLARE, a_card()) is ConditionVerdict.CLEAR


def test_one_specular_blob_is_glare() -> None:
    """A highlight bright enough to clip is glare too, and still measured here."""
    glared = a_colour_photograph()
    cv2.circle(glared, (600, 800), radius_for(0.02), (255, 255, 255), thickness=-1)

    assert severity(png(glared), QualityCondition.GLARE, a_card()) is QualityStatus.POOR


def test_a_large_specular_blob_is_unusable() -> None:
    glared = a_colour_photograph()
    cv2.circle(glared, (600, 800), radius_for(0.12), (255, 255, 255), thickness=-1)

    assert severity(png(glared), QualityCondition.GLARE, a_card()) is QualityStatus.UNUSABLE


def test_scattered_saturated_pixels_are_not_glare() -> None:
    """The measure is the largest *connected* region, not the reflecting fraction.

    A card with white text all over it has bright flat pixels everywhere and no
    glare at all; measuring the total would refuse it.
    """
    speckled = a_colour_photograph()
    rng = np.random.default_rng(2)
    ys = rng.integers(0, SIZE[0], size=40_000)
    xs = rng.integers(0, SIZE[1], size=40_000)
    speckled[ys, xs] = 255

    assert verdict(png(speckled), QualityCondition.GLARE, a_card()) is ConditionVerdict.CLEAR


def test_glare_is_measured_on_the_card_and_not_on_what_it_is_lying_on() -> None:
    """#190's mechanism, as a test: the same reflection off the frame is nothing.

    A desk lamp burning a hole in the tablecloth beside the card is not a
    defect in the card, and before #208 it was the only kind of glare the gate
    could see at all.
    """
    beside = a_colour_photograph()
    cv2.circle(beside, (80, 120), radius_for(0.12), (255, 255, 255), thickness=-1)

    assert verdict(png(beside), QualityCondition.GLARE, a_card()) is ConditionVerdict.CLEAR


def test_without_a_geometry_the_six_are_reported_undetermined() -> None:
    """The acceptance criterion's other half, and the degradation path."""
    report = assess(png(a_photograph()))

    for condition in NEEDS_CARD_GEOMETRY:
        finding = report.of(condition)
        assert finding.verdict is ConditionVerdict.UNDETERMINED, condition
        assert finding.reason
    assert report.detector is None


def test_a_detector_that_could_not_find_the_card_supplies_its_own_reason() -> None:
    """`None` and a failed detection are different facts, and both are stored."""
    excuse = "no card-like quadrilateral was found in the photograph"
    report = assess(png(a_photograph()), geometry=InsufficientInformation(excuse))

    assert report.of(QualityCondition.MULTIPLE_CARDS).reason == excuse
    assert report.status is QualityStatus.ACCEPTABLE
    assert report.detector is None


def test_a_reasonless_failure_still_says_something() -> None:
    """`undetermined` with no explanation is a gap wearing an answer's clothes."""
    report = assess(png(a_photograph()), geometry=INSUFFICIENT_INFORMATION)

    assert report.of(QualityCondition.MULTIPLE_CARDS).reason


def test_a_located_card_makes_a_clean_photograph_good() -> None:
    """The first status this project can reach that is not `acceptable`."""
    report = assess(png(a_photograph()), geometry=a_card())

    undetermined = [
        finding for finding in report.findings if finding.verdict is ConditionVerdict.UNDETERMINED
    ]
    assert undetermined == []
    assert report.status is QualityStatus.GOOD
    assert report.detector == DETECTOR


def geometric(condition: QualityCondition, geometry: CardGeometry) -> QualityStatus:
    finding = assess(png(a_photograph()), geometry=geometry).of(condition)
    assert finding.verdict is ConditionVerdict.DETECTED, finding
    assert finding.severity is not None
    return finding.severity


def test_a_second_card_in_the_frame_is_unusable() -> None:
    """Not `poor`: an analysis that picked one of two is confidently wrong."""
    assert (
        geometric(QualityCondition.MULTIPLE_CARDS, a_card(candidates=2)) is QualityStatus.UNUSABLE
    )


def test_a_card_running_off_the_edge_of_the_frame_is_unusable() -> None:
    height, width = SIZE
    clipped = a_card(
        corners=(
            (0.0, 0.0),
            (0.6 * width, 0.0),
            (0.6 * width, 0.9 * height),
            (0.0, 0.9 * height),
        )
    )

    assert geometric(QualityCondition.CARD_PARTLY_OUTSIDE_FRAME, clipped) is QualityStatus.UNUSABLE


def test_a_card_too_small_in_the_frame_is_detected() -> None:
    height, width = SIZE
    distant = a_card(
        corners=(
            (0.45 * width, 0.45 * height),
            (0.55 * width, 0.45 * height),
            (0.55 * width, 0.60 * height),
            (0.45 * width, 0.60 * height),
        )
    )

    assert geometric(QualityCondition.INSUFFICIENT_CARD_SIZE, distant) is QualityStatus.UNUSABLE


def tilted(top_inset: float) -> CardGeometry:
    """A card whose top edge is foreshortened against a 0.6-frame bottom edge."""
    height, width = SIZE
    return a_card(
        corners=(
            (top_inset * width, 0.15 * height),
            ((1.0 - top_inset) * width, 0.15 * height),
            (0.80 * width, 0.85 * height),
            (0.20 * width, 0.85 * height),
        )
    )


def test_a_card_held_at_a_mild_angle_is_poor_and_correctable() -> None:
    """0.25 to 0.75 is a 600 px top edge against a 720 px bottom: a 1.2."""
    assert (
        geometric(QualityCondition.SEVERE_PERSPECTIVE_DISTORTION, tilted(0.25))
        is QualityStatus.POOR
    )


def test_a_card_held_at_a_severe_angle_is_unusable() -> None:
    """0.32 to 0.68 is a 432 px top edge against 720: past correcting back."""
    assert (
        geometric(QualityCondition.SEVERE_PERSPECTIVE_DISTORTION, tilted(0.32))
        is QualityStatus.UNUSABLE
    )


def test_a_sleeve_costs_a_poor_and_never_a_refusal() -> None:
    """The weakest heuristic in the pipeline, so it may inform and not refuse.

    Asserted against the whole band rather than one value, so a recalibration
    that made a sleeve stop an analysis trips over this test.
    """
    for ratio in (1.03, 1.10, 1.25, 1.40, 1.50):
        assert (
            geometric(QualityCondition.SLEEVE_OBSTRUCTION, a_card(enclosing_ratio=ratio))
            is QualityStatus.POOR
        ), ratio


def test_the_geometric_thresholds_are_recorded_beside_the_detectors_own() -> None:
    """One flat record, and the two cannot collide: the detector's are prefixed."""
    detected = {"card_detection_work_long_edge": 1024.0}
    report = assess(png(a_photograph()), geometry=a_card(thresholds=detected))

    assert report.thresholds["work_long_edge"] == float(DEFAULT_THRESHOLDS.work_long_edge)
    assert report.thresholds["card_detection_work_long_edge"] == 1024.0


def test_a_report_answers_for_all_eleven_conditions() -> None:
    for geometry in (None, INSUFFICIENT_INFORMATION, a_card()):
        report = assess(png(a_photograph()), geometry=geometry)

        assert {finding.condition for finding in report.findings} == set(QualityCondition)


# ---------------------------------------------------------------------------
# The score
# ---------------------------------------------------------------------------


def test_the_score_is_a_fraction() -> None:
    assert 0.0 <= assess(png(a_photograph())).score <= 1.0


def test_a_detected_condition_and_a_score_below_a_half_are_the_same_fact() -> None:
    """Kept true by construction: the score is the smallest margin, and the poor
    threshold sits at 0.5. A score that could disagree with the status would be
    two answers to one question."""
    photographs = (
        png(a_photograph()),
        png(cv2.GaussianBlur(a_photograph(), (0, 0), sigmaX=8)),
        png((a_photograph() // 5).astype(np.uint8)),
    )
    geometries = (None, a_card(), a_card(candidates=2), a_card(enclosing_ratio=1.2))
    for data in photographs:
        for geometry in geometries:
            report = assess(data, geometry=geometry)
            detected = any(
                finding.verdict is ConditionVerdict.DETECTED for finding in report.findings
            )

            assert detected == (report.score < 0.5), report


def test_a_worse_photograph_scores_lower() -> None:
    """The score ranks the photographs the four statuses cannot tell apart."""
    clean = assess(png(a_photograph())).score
    softened = assess(png(cv2.GaussianBlur(a_photograph(), (0, 0), sigmaX=3))).score
    blurred = assess(png(cv2.GaussianBlur(a_photograph(), (0, 0), sigmaX=4))).score
    ruined = assess(png(cv2.GaussianBlur(a_photograph(), (0, 0), sigmaX=25))).score

    assert clean > softened > blurred > ruined


def test_the_score_bottoms_out_rather_than_going_negative() -> None:
    """Two hopeless photographs are both 0. The status is what distinguishes
    them, and there is nothing below unusable to distinguish."""
    ruined = assess(png(cv2.GaussianBlur(a_photograph(), (0, 0), sigmaX=25))).score

    assert ruined == 0.0


# ---------------------------------------------------------------------------
# What the verdict records about itself
# ---------------------------------------------------------------------------


def test_the_report_names_the_gate_that_produced_it() -> None:
    assert assess(png(a_photograph())).version == IMAGE_QUALITY_VERSION


def test_the_report_records_the_thresholds_it_ran_with() -> None:
    """So M7 can compare against this baseline without knowing what was configured."""
    report = assess(png(a_photograph()))

    assert report.thresholds == DEFAULT_THRESHOLDS.as_record()
    assert report.thresholds["blur_variance_poor"] == DEFAULT_THRESHOLDS.blur_variance_poor


def test_thresholds_are_a_parameter_rather_than_a_constant() -> None:
    """What "configurable" means here — see `thresholds.py`."""
    strict = QualityThresholds(
        blur_variance_unusable=8_000.0,
        blur_variance_poor=9_000.0,
        blur_variance_ideal=20_000.0,
    )
    data = png(a_photograph())

    assert verdict(data, QualityCondition.BLUR) is ConditionVerdict.CLEAR
    assert assess(data, thresholds=strict).of(QualityCondition.BLUR).verdict is (
        ConditionVerdict.DETECTED
    )


def test_a_triple_whose_unusable_threshold_is_not_the_stricter_one_is_refused() -> None:
    """A swapped pair reads as a working gate and is not one: an image would
    become unusable before it became poor."""
    with pytest.raises(ValueError, match="stricter"):
        QualityThresholds(blur_variance_unusable=120.0, blur_variance_poor=40.0)


def test_an_ideal_on_the_wrong_side_of_the_thresholds_is_refused() -> None:
    """The ordering *is* the direction — see `QualityThresholds`. An ideal that
    disagreed with it would invert every comparison for that condition."""
    with pytest.raises(ValueError, match="ideal better than both"):
        QualityThresholds(blur_variance_ideal=10.0)
