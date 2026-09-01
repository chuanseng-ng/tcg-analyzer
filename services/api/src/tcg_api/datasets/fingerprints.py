"""Perceptual fingerprints, and the near-duplicate relationship derived from them.

Spec §32 forbids splitting near-identical photographs of one card across train
and test, and #153's `uq_training_images_sha256` only catches the case where the
bytes are identical. A retake under different light is a different digest and the
same card, which is precisely the leakage §32 names — so this module answers
*which images look alike* over the standardized artifact, and #156's splitter
folds that answer into its grouping.

**Nothing here imports OpenCV or any `tcg_ml_*` package, and that is the point.**
Producing an artifact needs the CV stack and therefore the worker image;
*consuming* stored hashes needs neither, and #156's splitter is a plain command
over the database. If the grouping lived beside the warp, the splitter would
acquire OpenCV for a step it never runs — the same failure
`services/api/tests/test_import_purity.py` already guards one milestone earlier.
Pillow does the greyscale, the resize and the rotation; it is already a
`services/api` dependency, imported on the request path by
`tcg_api.analysis.image_validation`.

**Pairs and groups are computed, never stored.** The relationship is a pure
function of two hashes and a threshold, the threshold is not persisted, and a
stored pair is a second answer that drifts from the first the moment the number
moves — `market_snapshots` deriving its membership from a cut-line rather than
listing it is the same decision.

**What this cannot do, stated plainly.** A perceptual hash over a normalized
artifact is *designed* to collapse what two copies of one printing share, which
is the artwork; condition differences are sub-pixel at this scale. So two
genuinely different copies of one common card will be reported as near
duplicates. That is deliberate and it is the safe direction: over-grouping costs
a little balance in the split, and under-grouping leaks. Callers must not "fix"
it by tightening the threshold until the pair separates — the threshold would
then be tight enough to miss a real retake, which is the failure §32 exists to
prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Final

import sqlalchemy as sa
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncConnection
from tcg_domain.analysis import ImageSide

from tcg_api.datasets.tables import training_image_fingerprints, training_images

__all__ = [
    "DHASH_SIDE",
    "DHASH_VERSION",
    "NEAR_DUPLICATE_DISTANCE",
    "Fingerprint",
    "difference_hash",
    "distance",
    "hamming",
    "near_duplicate_groups",
    "near_duplicate_pairs",
    "read_fingerprints",
]

#: Comparisons per row and rows per hash: eight of each, so 64 bits. The resize
#: is to `DHASH_SIDE + 1` columns because the hash compares horizontally adjacent
#: pixels, and eight comparisons need nine samples.
DHASH_SIDE: Final = 8

#: What :func:`difference_hash` is. Part of the stored `hash_version`, so bumping
#: it re-fingerprints the corpus.
DHASH_VERSION: Final = "dhash-8x8-v0.1.0"

#: How many of the 64 bits may differ before two artifacts are the same
#: photograph. Prototyped against synthetic 756x1056 artifacts: a JPEG q60
#: round-trip moved 0 bits, a halve-and-restore 1, a 25% gain with an offset 4,
#: and fifteen pairs of different artwork sat between 27 and 43. Ten is between
#: the hardest true positive and the nearest false one, nearer the true side,
#: because a missed retake leaks and a spurious pair only costs balance.
#:
#: ponytail: PROVISIONAL. Chosen against synthetic fixtures, because no real card
#: photographs exist in this repository and none may be committed — ADR 0008
#: makes `redistribution_allowed` false on every approved source. Run
#: `tcg-detect-duplicate-training-images --measure` over a real directory and
#: read the valley off the histogram. The ceiling is narrower here than for a
#: general-purpose difference hash: every card of one set shares a border, a
#: frame and a layout, so the baseline between two *different printings* is lower
#: than between two arbitrary photographs and the valley may be shallow. If it is,
#: the upgrade is to hash a fixed artwork window of the artifact rather than the
#: whole of it — not a smaller number, and not a learned embedding, which is M7's.
NEAR_DUPLICATE_DISTANCE: Final = 10

#: The views that show the card's back — three of spec §52's six sides, not one.
#: `angled_back` and `surface_back` are the guided-photography flow, and they
#: photograph the same single printing that `back` does.
#:
#: Two images of the back carry no information about whether they are the same
#: *object*: every English Pokémon card has the one back, so the resemblance is
#: guaranteed and says nothing. #181 measured two different cards' backs at
#: exactly `NEAR_DUPLICATE_DISTANCE`, and the corpus pass then merged both copies
#: into one group through that link (#191).
_BACK_FACING: Final = frozenset({ImageSide.BACK, ImageSide.ANGLED_BACK, ImageSide.SURFACE_BACK})


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """One image's two hashes, as :func:`read_fingerprints` returns them.

    Args:
        training_image_id: The image these were taken over.
        perceptual_hash: 16 lowercase hex characters, or ``None`` when no card
            could be located in the bytes.
        perceptual_hash_rotated: The same for the artifact turned 180 degrees.
            ``None`` exactly when ``perceptual_hash`` is, which the schema's
            `both_hashes_or_neither` enforces.
        side: Which view of the card this is, from `training_images.side`, or
            ``None`` where nothing says — `deduplication.measure` reads a bare
            directory of files that were never ingested. It is a *grouping*
            input rather than a hash input: :func:`near_duplicate_pairs` drops
            the back-to-back link, and an unknown side is compared.

    **`side` has no default, deliberately.** It is NOT NULL one table over, and
    a default would have to be a front — the value that keeps a pair in the
    relation — so forgetting to pass one would quietly restore #191.
    """

    training_image_id: uuid.UUID
    perceptual_hash: str | None
    perceptual_hash_rotated: str | None
    side: ImageSide | None


def difference_hash(artifact: bytes) -> tuple[str, str]:
    """Hash one normalized artifact, upright and turned 180 degrees.

    Args:
        artifact: A PNG, the `Normalized.data` `ml/normalization` produced. The
            raw photograph is deliberately not what is hashed: the artifact has
            already had framing and perspective taken out of it, so two shots of
            one card from different angles compare as the same card for free.

    Returns:
        The upright hash and the rotated one, each 16 lowercase hex characters.

    The rotated hash exists because `Normalized.quarter_turns` is only 0 or 1: it
    puts the card's short edge first and makes no claim about which way up the
    card is *printed*, since only reading the artwork could say. So an artifact is
    in exactly one of two orientations — upright or turned — which is why two
    hashes suffice and four would be waste.

    **It is computed, not derived.** Turning the image 180 degrees reverses the
    direction of the left-to-right comparison this hash is built from, so the
    second value is not a bit reversal of the first. And canonicalising the two
    into one — taking the smaller, say — is the obvious wrong simplification: two
    near-identical artifacts can canonicalise to opposite orientations, at which
    point the distance between them is meaningless.

    `Image.Resampling.BOX` rather than a smoothing filter, for the reason
    `ml/normalization` gives for `INTER_AREA`: 756/9 and 1056/8 are both whole
    numbers, so this is an exact block average with no invented detail.
    """
    with Image.open(BytesIO(artifact)) as opened:
        greyscale = opened.convert("L")
        turned = greyscale.transpose(Image.Transpose.ROTATE_180)
        return _hash_one(greyscale), _hash_one(turned)


def _hash_one(greyscale: Image.Image) -> str:
    """One orientation's 64 bits, most significant first, as hex."""
    reduced = greyscale.resize((DHASH_SIDE + 1, DHASH_SIDE), Image.Resampling.BOX)
    # `tobytes()` rather than `getdata()`, which Pillow deprecates. An "L" image
    # is one byte per pixel in row-major order, so the arithmetic below indexes
    # it directly.
    pixels = reduced.tobytes()
    bits = 0
    for row in range(DHASH_SIDE):
        start = row * (DHASH_SIDE + 1)
        for column in range(DHASH_SIDE):
            # Strictly greater, so a flat run reads as zeros rather than as
            # whatever the tie-break happened to be.
            bits = (bits << 1) | int(pixels[start + column] > pixels[start + column + 1])
    return f"{bits:016x}"


def hamming(left: str, right: str) -> int:
    """How many of the 64 bits differ between two hex hashes."""
    return (int(left, 16) ^ int(right, 16)).bit_count()


def distance(left: Fingerprint, right: Fingerprint) -> int | None:
    """How alike two images are, whichever way up either was photographed.

    Returns ``None`` when either side has no hash — no card was located in it, so
    there is nothing to compare and it is never a duplicate of anything.

    **Three terms, not two.** The obvious
    ``min(H(a.upright, b.upright), H(a.upright, b.rotated))`` is *asymmetric*:
    measured over synthetic fixtures, 23 of 55 pairs disagreed when the arguments
    were swapped, which would make grouping depend on iteration order. Adding
    ``H(a.rotated, b.upright)`` makes the set of terms invariant under a swap, so
    the function is symmetric by construction rather than by argument. The fourth
    term, ``H(a.rotated, b.rotated)``, covers no orientation the three do not:
    turning both is the same relative alignment as turning neither.
    """
    if left.perceptual_hash is None or right.perceptual_hash is None:
        return None
    if left.perceptual_hash_rotated is None or right.perceptual_hash_rotated is None:
        return None
    return min(
        hamming(left.perceptual_hash, right.perceptual_hash),
        hamming(left.perceptual_hash, right.perceptual_hash_rotated),
        hamming(left.perceptual_hash_rotated, right.perceptual_hash),
    )


def near_duplicate_pairs(
    fingerprints: Sequence[Fingerprint],
    *,
    threshold: int = NEAR_DUPLICATE_DISTANCE,
) -> tuple[tuple[uuid.UUID, uuid.UUID, int], ...]:
    """Every pair within `threshold`, with the distance that put it there.

    Ordered by distance and then by identifier, so a report reads closest first
    and two runs over one corpus produce the same text.

    **Two images of the card's back are never a pair** (#191). The hash answers
    "do these look alike", and two backs look alike because every English card
    shares one printing — the resemblance is guaranteed and therefore carries no
    information about whether they photograph the same object. Left in it is not
    a harmless extra link: the grouping below is a transitive closure, so
    back-to-back edges chain unrelated copies together and, as the corpus grows,
    collapse it toward one group — which is two empty splits and no splitter.

    Nothing is lost by dropping it. A copy's own front and back already group
    through `physical_copy_id`, which #156 unions first, so the only thing a
    back-to-back edge ever adds is a link between *different* copies — the exact
    link that is wrong. Front-to-front stays, because front artwork differs per
    card and a match there is real evidence; front-to-back stays too, because
    that is either a mislabelled row or one photograph ingested twice, and both
    are worth grouping.

    **This is not a threshold change.** `NEAR_DUPLICATE_DISTANCE` stays where
    #155 put it and stays unstored. Tightening it cannot separate two copies of
    one printing — #155 says so in as many words — and the retake side of the
    valley is still unmeasured, so there is no number to move it to.

    ponytail: O(n²), and deliberately — the issue asks for it in as many words.
    ADR 0008's four approved sources produce a corpus in the low thousands, and
    5,000 images is 12.5M popcounts, about a second. The upgrade path is an LSH
    index over hash prefixes, and it is not worth reaching for until this pass
    costs more than the fingerprinting it follows.
    """
    found: list[tuple[uuid.UUID, uuid.UUID, int]] = []
    for index, left in enumerate(fingerprints):
        for right in fingerprints[index + 1 :]:
            if left.side in _BACK_FACING and right.side in _BACK_FACING:
                continue
            apart = distance(left, right)
            if apart is not None and apart <= threshold:
                found.append((left.training_image_id, right.training_image_id, apart))
    return tuple(sorted(found, key=lambda pair: (pair[2], str(pair[0]), str(pair[1]))))


def near_duplicate_groups(
    fingerprints: Sequence[Fingerprint],
    *,
    threshold: int = NEAR_DUPLICATE_DISTANCE,
) -> tuple[frozenset[uuid.UUID], ...]:
    """The disjoint groups §32's splitter must not break apart.

    The transitive closure of :func:`near_duplicate_pairs`, because the splitter
    needs groups rather than pairs: a chain where A resembles B and B resembles C
    but A does not resemble C must still land whole on one side of the split.

    Singletons are excluded — an image that resembles nothing is not a finding,
    and #156 groups it by `physical_copy_id` or by `source` as §32 directs.

    ponytail: a single spurious fingerprint bridging two clusters merges both,
    and the merge is unbounded. Accepted, because over-grouping costs balance and
    under-grouping leaks — but the operator report prints group sizes so a group
    of four hundred is visible rather than silent.
    """
    parent: dict[uuid.UUID, uuid.UUID] = {}

    def find(node: uuid.UUID) -> uuid.UUID:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right, _ in near_duplicate_pairs(fingerprints, threshold=threshold):
        parent[find(left)] = find(right)

    grouped: dict[uuid.UUID, set[uuid.UUID]] = {}
    for member in parent:
        grouped.setdefault(find(member), set()).add(member)

    return tuple(
        sorted(
            (frozenset(members) for members in grouped.values() if len(members) > 1),
            key=lambda group: (-len(group), sorted(str(member) for member in group)),
        )
    )


async def read_fingerprints(connection: AsyncConnection) -> tuple[Fingerprint, ...]:
    """Every stored fingerprint, in insertion order.

    The whole table, because the comparison is pairwise and there is no query
    that narrows it. #156 calls this and nothing else in this domain.

    Joined to `training_images` for `side`, which #191 needs to drop the
    back-to-back link. An inner join: the fingerprint's foreign key is NOT NULL
    and so is the side, so a fingerprint without one cannot exist and an outer
    join would only invent a NULL to think about.
    """
    rows = await connection.execute(
        sa.select(
            training_image_fingerprints.c.training_image_id,
            training_image_fingerprints.c.perceptual_hash,
            training_image_fingerprints.c.perceptual_hash_rotated,
            training_images.c.side,
        )
        .select_from(
            training_image_fingerprints.join(
                training_images,
                training_image_fingerprints.c.training_image_id == training_images.c.id,
            )
        )
        .order_by(training_image_fingerprints.c.computed_at)
    )
    return tuple(
        Fingerprint(
            training_image_id=row.training_image_id,
            perceptual_hash=row.perceptual_hash,
            perceptual_hash_rotated=row.perceptual_hash_rotated,
            side=ImageSide(row.side),
        )
        for row in rows
    )
