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


def verdict(data: bytes, condition: QualityCondition) -> ConditionVerdict:
    return assess(data).of(condition).verdict


def severity(data: bytes, condition: QualityCondition) -> QualityStatus | None:
    return assess(data).of(condition).severity


# ---------------------------------------------------------------------------
# A photograph with nothing wrong with it
# ---------------------------------------------------------------------------


def test_an_ordinary_photograph_trips_none_of_the_six() -> None:
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
# The six conditions this gate decides
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


def test_one_specular_blob_is_glare() -> None:
    glared = a_photograph()
    cv2.circle(glared, (600, 800), 110, (255, 255, 255), thickness=-1)

    assert verdict(png(glared), QualityCondition.GLARE) is ConditionVerdict.DETECTED


def test_scattered_saturated_pixels_are_not_glare() -> None:
    """The measure is the largest *connected* region, not the saturated fraction.

    A card with a white border on a white desk saturates a great deal of the
    frame and has no glare at all; measuring the total would refuse it.
    """
    speckled = a_photograph()
    rng = np.random.default_rng(2)
    ys = rng.integers(0, SIZE[0], size=40_000)
    xs = rng.integers(0, SIZE[1], size=40_000)
    speckled[ys, xs] = 255

    assert verdict(png(speckled), QualityCondition.GLARE) is ConditionVerdict.CLEAR


def test_a_large_specular_blob_is_unusable() -> None:
    glared = a_photograph()
    cv2.circle(glared, (600, 800), 300, (255, 255, 255), thickness=-1)

    assert severity(png(glared), QualityCondition.GLARE) is QualityStatus.UNUSABLE


# ---------------------------------------------------------------------------
# The five this gate cannot decide, and says so
# ---------------------------------------------------------------------------


def test_every_condition_that_needs_the_card_located_is_reported_undetermined() -> None:
    """The acceptance criterion's other half. #37 supplies the geometry."""
    report = assess(png(a_photograph()))

    for condition in NEEDS_CARD_GEOMETRY:
        finding = report.of(condition)
        assert finding.verdict is ConditionVerdict.UNDETERMINED, condition
        assert finding.reason


def test_an_otherwise_clean_photograph_is_acceptable_rather_than_good() -> None:
    """Because five conditions were not checked. `good` waits for #37."""
    assert assess(png(a_photograph())).status is QualityStatus.ACCEPTABLE


def test_a_report_answers_for_all_eleven_conditions() -> None:
    report = assess(png(a_photograph()))

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
    for data in (
        png(a_photograph()),
        png(cv2.GaussianBlur(a_photograph(), (0, 0), sigmaX=8)),
        png((a_photograph() // 5).astype(np.uint8)),
    ):
        report = assess(data)
        detected = any(finding.verdict is ConditionVerdict.DETECTED for finding in report.findings)

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
