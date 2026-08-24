"""What a market snapshot refuses to be.

Four fields, two of which the database writes and one of which it generates, so
there is little here to get wrong — which is the point. The rules that matter
are the two a row could violate on the way out: an instant with no offset, and a
`data_version` that is not a date. `tcg_api.market.snapshots` constructs one of
these for every row it reads, so these are the checks that stand between a bad
row and a caller.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from tcg_market_data import InvalidMarketSnapshot, MarketSnapshot

CUT = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


def a_snapshot(**overrides: object) -> MarketSnapshot:
    values: dict[str, object] = {
        "id": uuid4(),
        "provider": uuid4(),
        "generated_at": CUT,
        "data_version": date(2026, 8, 24),
    }
    values.update(overrides)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def test_a_snapshot_is_frozen() -> None:
    """§36's whole claim. A snapshot that could be edited is not a record of anything."""
    snapshot = a_snapshot()

    with pytest.raises(AttributeError):
        snapshot.generated_at = CUT  # type: ignore[misc]


def test_a_naive_cut_is_refused() -> None:
    """A cut-line without an offset moves by the reader's timezone.

    Which would silently change which observations a snapshot comprises — the
    one thing that must never happen to one.
    """
    with pytest.raises(InvalidMarketSnapshot, match="timezone-aware"):
        a_snapshot(generated_at=datetime(2026, 8, 24, 3, 0))  # noqa: DTZ001 — the point


def test_a_data_version_that_is_not_a_date_is_refused() -> None:
    """ADR 0006 fixes the content: no provider publishes a version, so it is the date."""
    with pytest.raises(InvalidMarketSnapshot, match="data_version must be a date"):
        a_snapshot(data_version="2026-08-24")


def test_a_datetime_is_not_a_date() -> None:
    """`date` is a `datetime` superclass, so this is the check that is easy to lose."""
    with pytest.raises(InvalidMarketSnapshot, match="data_version must be a date"):
        a_snapshot(data_version=CUT)


def test_the_provider_is_an_identifier_not_a_slug() -> None:
    """§36 names `provider`; what a snapshot stores is the `market_providers` row.

    The slug lives on that row, and one fact belongs in one place.
    """
    with pytest.raises(InvalidMarketSnapshot, match="provider must be"):
        a_snapshot(provider="pokepricetracker")


def test_an_identifier_that_is_not_a_uuid_is_refused() -> None:
    with pytest.raises(InvalidMarketSnapshot, match="id must be a UUID"):
        a_snapshot(id="66666666-6666-5666-8666-666666666666")


def test_a_snapshot_says_what_it_is() -> None:
    """The date stamp ADR 0006 requires beside a price, and the cut behind it."""
    assert str(a_snapshot()) == "2026-08-24 @ 2026-08-24T03:00:00+00:00"


def test_two_snapshots_of_the_same_row_are_equal() -> None:
    """Value equality is what lets a test assert a re-resolved snapshot is the one."""
    identifier, provider = uuid4(), uuid4()

    assert a_snapshot(id=identifier, provider=provider) == a_snapshot(
        id=identifier, provider=provider
    )


def test_the_fields_are_exactly_section_36s() -> None:
    """`observations` is deliberately not among them — a snapshot resolves them."""
    assert MarketSnapshot.__dataclass_fields__.keys() == {
        "id",
        "provider",
        "generated_at",
        "data_version",
    }


def test_the_identifier_survives_a_round_trip() -> None:
    """What `analyses.market_snapshot_id` holds is exactly this."""
    identifier = UUID("66666666-6666-5666-8666-666666666666")

    assert a_snapshot(id=identifier).id == identifier
