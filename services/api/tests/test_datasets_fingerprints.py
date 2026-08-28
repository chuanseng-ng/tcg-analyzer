"""The perceptual hash, the distance and the grouping — #155.

No PostgreSQL, no OpenCV. Everything asserted here is a claim about
`tcg_api.datasets.fingerprints`, which is deliberately reachable without the CV
stack so that #156's splitter can consume stored hashes from the API image —
`test_import_purity.py` is what holds that.

Artifacts are built directly rather than photographed and warped: this module is
about what happens *after* `ml/normalization` has run, and building 756x1056
PNGs here keeps these tests hermetic. The end-to-end claim — a real photograph,
detected, warped and hashed — is `test_datasets_deduplication.py`'s, where the
CV stack is imported on purpose.

**One test the issue asked for is deliberately not here**, and its absence is
the decision rather than an omission: see
`test_two_different_printings_are_not_linked`.
"""

from __future__ import annotations

import re
import uuid
from io import BytesIO

import numpy as np
import pytest
from numpy.typing import NDArray
from PIL import Image
from tcg_api.datasets.fingerprints import (
    DHASH_SIDE,
    NEAR_DUPLICATE_DISTANCE,
    Fingerprint,
    difference_hash,
    distance,
    hamming,
    near_duplicate_groups,
    near_duplicate_pairs,
)
from tcg_api.datasets.tables import _PERCEPTUAL_HASH_PATTERN

WIDTH = 756
HEIGHT = 1056


def artwork(seed: int) -> NDArray[np.uint8]:
    """A card-shaped raster with coarse structure and grain.

    Structure rather than a flat fill, because a difference hash over a uniform
    image is all zeros and every assertion below would pass vacuously. The coarse
    block layout stands in for artwork; the grain stands in for print texture and
    is what a recompression has to survive.
    """
    generator = np.random.default_rng(seed)
    blocks = generator.integers(0, 255, (11, 8), dtype=np.uint8)
    grown = np.array(Image.fromarray(blocks).resize((WIDTH, HEIGHT), Image.Resampling.BICUBIC))
    grain = generator.integers(-12, 12, (HEIGHT, WIDTH))
    return np.clip(grown.astype(np.int16) + grain, 0, 255).astype(np.uint8)


def png(raster: NDArray[np.uint8]) -> bytes:
    buffer = BytesIO()
    Image.fromarray(raster).save(buffer, format="PNG")
    return buffer.getvalue()


def fingerprint(artifact: bytes, identifier: uuid.UUID | None = None) -> Fingerprint:
    upright, rotated = difference_hash(artifact)
    return Fingerprint(
        training_image_id=identifier or uuid.uuid4(),
        perceptual_hash=upright,
        perceptual_hash_rotated=rotated,
    )


def apart(left: bytes, right: bytes) -> int:
    measured = distance(fingerprint(left), fingerprint(right))
    assert measured is not None
    return measured


# ---------------------------------------------------------------------------
# The hash itself
# ---------------------------------------------------------------------------


def test_the_same_bytes_hash_the_same() -> None:
    """Nothing here is seeded from a clock or a random source."""
    artifact = png(artwork(1))

    assert difference_hash(artifact) == difference_hash(artifact)


def test_the_hash_is_sixteen_lowercase_hex_characters() -> None:
    upright, rotated = difference_hash(png(artwork(1)))

    assert re.fullmatch("[0-9a-f]{16}", upright)
    assert re.fullmatch("[0-9a-f]{16}", rotated)


def test_the_hash_grammar_this_module_produces_is_the_one_the_schema_enforces() -> None:
    """The 16 characters here and the CHECK in `tables.py` are one claim.

    Without this, widening the hash to 128 bits would leave the schema silently
    refusing every row the new hash produces.
    """
    upright, rotated = difference_hash(png(artwork(1)))

    assert re.fullmatch(_PERCEPTUAL_HASH_PATTERN.strip("^$"), upright)
    assert re.fullmatch(_PERCEPTUAL_HASH_PATTERN.strip("^$"), rotated)


def test_the_hash_is_sixty_four_bits() -> None:
    assert DHASH_SIDE * DHASH_SIDE == 64


# ---------------------------------------------------------------------------
# What is a near duplicate, and what is not
# ---------------------------------------------------------------------------


def test_a_recompressed_copy_is_a_near_duplicate() -> None:
    """The same photograph through a lossy encoder — the issue's second test."""
    original = artwork(1)
    buffer = BytesIO()
    Image.fromarray(original).save(buffer, format="JPEG", quality=60)
    with Image.open(BytesIO(buffer.getvalue())) as decoded:
        recompressed = np.array(decoded.convert("L"))

    assert apart(png(original), png(recompressed)) <= NEAR_DUPLICATE_DISTANCE


def test_a_resized_copy_is_a_near_duplicate() -> None:
    """Halved and restored, which is what a downscaled export looks like."""
    original = artwork(1)
    with Image.fromarray(original) as image:
        small = image.resize((WIDTH // 2, HEIGHT // 2), Image.Resampling.BOX)
        restored = np.array(small.resize((WIDTH, HEIGHT), Image.Resampling.BOX))

    assert apart(png(original), png(restored)) <= NEAR_DUPLICATE_DISTANCE


def test_the_same_card_under_different_light_is_a_near_duplicate() -> None:
    """A retake, which is the case `sha256` cannot catch and §32 is about."""
    original = artwork(1)
    brighter = np.clip(original.astype(np.float32) * 1.25 + 12, 0, 255).astype(np.uint8)

    assert apart(png(original), png(brighter)) <= NEAR_DUPLICATE_DISTANCE


def test_two_different_printings_are_not_linked() -> None:
    """Different artwork is not a duplicate — and note what this does *not* say.

    The issue asks that "two different cards of the same printing" be
    distinguished. They cannot be, and the substitution is a decision rather than
    a shortcut: a perceptual hash over a normalized artifact is *designed* to
    collapse what two copies of one printing share, and condition differences are
    sub-pixel at 8x8. Tightening the threshold until such a pair separated would
    make it tight enough to miss the retake the test above covers, which is the
    leakage §32 exists to prevent. So two copies of one printing are grouped, on
    purpose, and this asserts the claim that *is* true.
    """
    assert apart(png(artwork(1)), png(artwork(2))) > NEAR_DUPLICATE_DISTANCE


@pytest.mark.parametrize("seed", range(3, 9))
def test_different_artwork_stays_far_from_the_threshold(seed: int) -> None:
    """Not merely over the line — well over it, so the margin is visible."""
    assert apart(png(artwork(1)), png(artwork(seed))) > 2 * NEAR_DUPLICATE_DISTANCE


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------


def test_an_upside_down_copy_is_a_near_duplicate() -> None:
    """`quarter_turns` fixes the aspect and not which way up the card is printed.

    The second assertion is the point: without the rotated hash, the two would be
    far apart, so deleting that column would fail this test rather than leave it
    quietly passing.
    """
    original = png(artwork(1))
    turned = png(np.rot90(artwork(1), 2))

    assert apart(original, turned) <= NEAR_DUPLICATE_DISTANCE

    upright_only = hamming(difference_hash(original)[0], difference_hash(turned)[0])
    assert upright_only > 2 * NEAR_DUPLICATE_DISTANCE


@pytest.mark.parametrize("seed", range(1, 12))
def test_distance_is_symmetric(seed: int) -> None:
    """Three terms rather than two, so grouping cannot depend on iteration order.

    The two-term form `min(H(a, b), H(a, b_rotated))` disagrees with itself under
    a swap on roughly half of these pairs.
    """
    left = fingerprint(png(artwork(seed)))
    right = fingerprint(png(artwork(seed + 20)))

    assert distance(left, right) == distance(right, left)


def test_an_image_with_no_hash_is_a_duplicate_of_nothing() -> None:
    """No card was located in it, so there is nothing to compare."""
    located = fingerprint(png(artwork(1)))
    unlocatable = Fingerprint(uuid.uuid4(), perceptual_hash=None, perceptual_hash_rotated=None)

    assert distance(located, unlocatable) is None
    assert distance(unlocatable, unlocatable) is None
    assert near_duplicate_groups((located, unlocatable)) == ()


# ---------------------------------------------------------------------------
# The threshold
# ---------------------------------------------------------------------------


def test_the_threshold_is_the_chosen_one() -> None:
    """Pinned by value, so it cannot move without somebody meaning it to.

    Changing this changes which images §32's splitter refuses to separate, and a
    threshold nobody checked is worse than no deduplication. Move it from a
    `--measure` histogram over real photographs, never from an intuition — and
    note that until somebody does, the issue's "measured against real images"
    criterion is outstanding.
    """
    assert NEAR_DUPLICATE_DISTANCE == 10


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def hashed(*values: str) -> tuple[Fingerprint, ...]:
    """Fingerprints from hand-written hex, so grouping is tested without images."""
    return tuple(
        Fingerprint(
            training_image_id=uuid.UUID(int=index + 1),
            perceptual_hash=value,
            perceptual_hash_rotated=value,
        )
        for index, value in enumerate(values)
    )


def test_a_lone_image_is_not_a_group() -> None:
    assert near_duplicate_groups(hashed("0000000000000000", "ffffffffffffffff")) == ()


def test_a_matching_pair_is_one_group_of_two() -> None:
    groups = near_duplicate_groups(hashed("0000000000000000", "0000000000000001"))

    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_groups_are_transitively_closed() -> None:
    """A resembles B and B resembles C, but A does not resemble C.

    All three must still land on one side of the split, or the chain leaks.
    """
    # 0, 8 and 16 bits set: the ends are 16 apart, past the threshold, while
    # each neighbouring pair is 8 apart and within it.
    groups = near_duplicate_groups(
        hashed("0000000000000000", "00000000000000ff", "000000000000ffff")
    )

    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_two_clusters_stay_two_groups() -> None:
    groups = near_duplicate_groups(
        hashed("0000000000000000", "0000000000000001", "ffffffffffffffff", "fffffffffffffffe")
    )

    assert len(groups) == 2
    assert sorted(len(group) for group in groups) == [2, 2]


def test_pairs_carry_the_distance_and_read_closest_first() -> None:
    pairs = near_duplicate_pairs(hashed("0000000000000000", "0000000000000001", "0000000000000007"))

    assert [apart for _, _, apart in pairs] == sorted(apart for _, _, apart in pairs)
    assert pairs[0][2] == 1


def test_the_threshold_is_inclusive() -> None:
    """Exactly at the line is a duplicate; one bit past it is not."""
    at = "0" * 6 + f"{(1 << NEAR_DUPLICATE_DISTANCE) - 1:010x}"
    past = "0" * 6 + f"{(1 << (NEAR_DUPLICATE_DISTANCE + 1)) - 1:010x}"

    assert len(near_duplicate_groups(hashed("0000000000000000", at))) == 1
    assert near_duplicate_groups(hashed("0000000000000000", past)) == ()
