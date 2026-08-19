"""Unit tests for the analysis vocabularies.

These are transcriptions of closed lists in the specification, so the tests are
transcriptions too: the values are written out again here rather than derived
from the enums, because a test that reads its expectation from the code under
test proves only that the code equals itself.
"""

from __future__ import annotations

import pytest
from tcg_domain.analysis import (
    TERMINAL_STATUSES,
    V1_SIDES,
    AnalysisStatus,
    ImageSide,
    QualityStatus,
    SessionStatus,
)

# Spec §65, in the order the specification lists them.
SECTION_65_STATES = (
    "created",
    "uploading",
    "uploaded",
    "identifying",
    "awaiting_confirmation",
    "analyzing",
    "calculating",
    "completed",
    "failed",
)

# Spec §11: two values, then the four §52 names as "Future".
SECTION_11_SIDES = (
    "front",
    "back",
    "angled_front",
    "angled_back",
    "surface_front",
    "surface_back",
)

# Spec §19.
SECTION_19_STATUSES = ("good", "acceptable", "poor", "unusable")


def test_the_nine_analysis_states_are_section_65s() -> None:
    assert tuple(status.value for status in AnalysisStatus) == SECTION_65_STATES


def test_the_six_sides_are_section_11s() -> None:
    """Including §52's four, which is what lets guided photography skip a migration."""
    assert tuple(side.value for side in ImageSide) == SECTION_11_SIDES


def test_the_four_quality_statuses_are_section_19s() -> None:
    assert tuple(status.value for status in QualityStatus) == SECTION_19_STATUSES


def test_a_session_is_usable_lapsed_or_emptied() -> None:
    """This vocabulary is this project's: §12 names the column and not its values."""
    assert tuple(status.value for status in SessionStatus) == ("active", "expired", "purged")


def test_queued_is_not_an_analysis_state() -> None:
    """§65 has the run endpoint answer `queued` and then lists nine states without it.

    It is a transport word for "accepted, not started". A tenth member would put
    it in the database, where nothing could ever leave it.
    """
    assert "queued" not in {status.value for status in AnalysisStatus}


@pytest.mark.parametrize(
    "vocabulary",
    [AnalysisStatus, SessionStatus, ImageSide, QualityStatus],
    ids=["analysis", "session", "side", "quality"],
)
def test_members_are_strings(vocabulary: type[AnalysisStatus]) -> None:
    """`StrEnum`, on the same terms as `Game` and `Language`.

    A member may be passed anywhere a `str` is wanted, so nothing has to remember
    to write `.value` before handing one to a query or a response model.
    """
    for member in vocabulary:
        assert isinstance(member, str)
        assert member == member.value


def test_the_terminal_states_are_the_two_an_analysis_does_not_leave() -> None:
    assert {AnalysisStatus.COMPLETED, AnalysisStatus.FAILED} == TERMINAL_STATUSES
    assert set(AnalysisStatus) >= TERMINAL_STATUSES


def test_v1_captures_a_front_and_a_back() -> None:
    """The other four sides are storable and unwritten until guided photography."""
    assert V1_SIDES == (ImageSide.FRONT, ImageSide.BACK)
    assert set(V1_SIDES) < set(ImageSide)
