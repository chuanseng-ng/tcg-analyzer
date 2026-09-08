"""Return codes — #270, spec §68.

The two claims worth testing are both about transcription rather than
cryptography: a code copied onto paper and typed back must reach the same row,
and a rendered code must be unable to sit in the column that stores its digest.
"""

from __future__ import annotations

import pytest
from tcg_api.codes import (
    ALPHABET,
    GROUP_SIZE,
    GROUPS,
    LENGTH,
    InvalidReturnCode,
    digest,
    mint,
    normalise,
    render,
)

#: The glyph pairs a handwritten code loses. Absent from what is rendered, and
#: folded on the way back in.
AMBIGUOUS = "ILOU"


def test_a_minted_code_is_grouped_for_a_person_to_copy() -> None:
    code = mint()

    groups = code.split("-")
    assert len(groups) == GROUPS
    assert {len(group) for group in groups} == {GROUP_SIZE}
    assert all(symbol in ALPHABET for group in groups for symbol in group)


def test_the_alphabet_holds_no_glyph_a_person_would_mistake() -> None:
    """Crockford's point: the renderer never emits one, so folding is lossless."""
    for glyph in AMBIGUOUS:
        assert glyph not in ALPHABET


def test_the_alphabet_is_upper_case_so_a_code_cannot_be_stored_as_a_digest() -> None:
    """`return_code_hash` takes 64 *lowercase* hex. Disjoint by construction."""
    assert ALPHABET.upper() == ALPHABET


def test_two_mints_differ() -> None:
    """A hundred bits: a collision here means `secrets` was not used."""
    assert len({mint() for _ in range(1000)}) == 1000


def test_a_code_written_down_badly_still_reaches_its_row() -> None:
    """The whole reason this module is not `code.upper().replace('-', '')`."""
    code = "A3KDM-9F2QT-BXWR7-N0HJ5"

    assert digest(code) == digest(code.lower())
    assert digest(code) == digest(code.replace("-", ""))
    assert digest(code) == digest(f"  {code}  ")
    assert digest(code) == digest(code.replace("-", " "))


@pytest.mark.parametrize(
    ("written", "meant"),
    [("O", "0"), ("o", "0"), ("I", "1"), ("i", "1"), ("L", "1"), ("l", "1")],
)
def test_the_glyphs_a_person_confuses_are_folded(written: str, meant: str) -> None:
    canonical = "0" * (LENGTH - 1) + meant
    mistyped = "0" * (LENGTH - 1) + written

    assert normalise(mistyped) == canonical


def test_a_digest_is_what_the_column_accepts() -> None:
    """64 lowercase hex — `return_code_is_stored_as_a_digest`, from the outside."""
    value = digest(mint())

    assert len(value) == 64
    assert all(character in "0123456789abcdef" for character in value)


def test_rendering_round_trips_through_normalising() -> None:
    symbols = ALPHABET[:LENGTH]

    assert normalise(render(symbols)) == symbols


@pytest.mark.parametrize(
    "written",
    [
        "",
        "A3KDM",
        "A3KDM-9F2QT-BXWR7-N0HJ5-EXTRA",
        "A3KDM-9F2QT-BXWR7-N0HJ",
        "U3KDM-9F2QT-BXWR7-N0HJ5",
        "A3KDM-9F2QT-BXWR7-N0HJ!",
    ],
)
def test_a_string_that_is_not_a_code_is_refused(written: str) -> None:
    """`U` is refused rather than folded: it is not on the alphabet and it is
    not a glyph anything is mistaken for, so it is a typo the caller should see
    as a miss rather than a different code."""
    with pytest.raises(InvalidReturnCode):
        normalise(written)


def test_rendering_the_wrong_number_of_symbols_is_refused() -> None:
    with pytest.raises(InvalidReturnCode):
        render("TOOSHORT")
