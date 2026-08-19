"""Unit tests for spec §57's `card_database_version` — issue #27.

Two things are being defended here. The first is that the identifier is
*explicit and ordered*: spec §31 forbids a version that names a moving target,
and a reproducibility record built on `/latest/` records nothing at all. The
second is that the record is a fact — a timestamp with a timezone, counts that
are counts — because it is the evidence an analysis is re-derived from long
after the run that produced it.

The port's contract is exercised against an in-memory implementation defined
below, on the same terms as `test_domain_repository.py`: a fake that lives with
the test rather than in `src`, so nothing ships an implementation the product
does not use.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from tcg_domain.catalog_version import (
    CardDatabaseVersion,
    CardDatabaseVersionRepository,
)
from tcg_domain.errors import DomainError, InvalidCatalogRecord

GENERATED_AT = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


def version(**overrides: object) -> CardDatabaseVersion:
    fields: dict[str, object] = {
        "version": "pokemon-catalog-v0.3.0",
        "source": "tcgdex",
        "generated_at": GENERATED_AT,
        "set_count": 4,
        "card_count": 20,
        "external_id_count": 21,
    }
    fields.update(overrides)
    return CardDatabaseVersion(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The record itself
# ---------------------------------------------------------------------------
def test_a_version_records_what_was_imported_from_where_and_when() -> None:
    record = version(source_license="MIT", source_revision="8f2c1ab")

    assert record.version == "pokemon-catalog-v0.3.0"
    assert record.source == "tcgdex"
    assert record.source_license == "MIT"
    assert record.source_revision == "8f2c1ab"
    assert record.generated_at == GENERATED_AT
    assert (record.set_count, record.card_count, record.external_id_count) == (4, 20, 21)


def test_provenance_beyond_the_source_is_optional() -> None:
    """The hand-authored fixtures have no upstream licence and no revision."""
    record = version(source="manual")

    assert record.source_license is None
    assert record.source_revision is None


def test_a_version_is_frozen() -> None:
    record = version()

    with pytest.raises(AttributeError):
        record.version = "pokemon-catalog-v0.4.0"  # type: ignore[misc]


def test_a_version_prints_as_its_identifier() -> None:
    assert str(version()) == "pokemon-catalog-v0.3.0"


# ---------------------------------------------------------------------------
# Spec §31 — the identifier is explicit and ordered, never a pointer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "identifier",
    [
        "pokemon-catalog-v0.3.0",
        "pokemon-catalog-seed-v0.0.0",
        "pokemon-catalog-v12.4.7",
        "onepiece-catalog-v1.0.0",
    ],
)
def test_an_explicit_ordered_identifier_is_accepted(identifier: str) -> None:
    assert version(version=identifier).version == identifier


@pytest.mark.parametrize(
    "identifier",
    ["latest", "/latest/", "pokemon-catalog-latest", "pokemon-catalog-vlatest"],
)
def test_a_moving_pointer_is_refused(identifier: str) -> None:
    """Spec §31: "a model must never simply reference /latest/"."""
    with pytest.raises(InvalidCatalogRecord, match="moving pointer"):
        version(version=identifier)


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "Pokemon-Catalog-v0.3.0",
        "pokemon-catalog-v0.3",
        "pokemon-catalog-0.3.0",
        "pokemon catalog v0.3.0",
        "v0.3.0",
        "-pokemon-catalog-v0.3.0",
    ],
)
def test_a_malformed_identifier_is_refused(identifier: str) -> None:
    with pytest.raises(InvalidCatalogRecord, match=re.escape("pokemon-catalog-v0.3.0")):
        version(version=identifier)


def test_a_non_string_identifier_is_refused() -> None:
    with pytest.raises(InvalidCatalogRecord, match="must be a string"):
        version(version=3)


# ---------------------------------------------------------------------------
# The rest of the record
# ---------------------------------------------------------------------------
def test_the_source_must_be_a_slug() -> None:
    with pytest.raises(InvalidCatalogRecord, match="lowercase slug"):
        version(source="TCGdex")


def test_a_naive_timestamp_is_refused() -> None:
    """ "Some o'clock, somewhere" is not a fact a reproducibility record carries."""
    with pytest.raises(InvalidCatalogRecord, match="must carry a timezone"):
        version(generated_at=datetime(2026, 8, 18, 12, 0))  # noqa: DTZ001


def test_a_calendar_date_is_refused_where_a_timestamp_is_meant() -> None:
    with pytest.raises(InvalidCatalogRecord, match="must be a datetime"):
        version(generated_at=date(2026, 8, 18))


def test_a_timestamp_in_another_timezone_is_accepted() -> None:
    singapore = datetime(2026, 8, 18, 20, 0, tzinfo=timezone(timedelta(hours=8)))

    assert version(generated_at=singapore).generated_at == GENERATED_AT


@pytest.mark.parametrize("field", ["set_count", "card_count", "external_id_count"])
def test_a_negative_count_is_refused(field: str) -> None:
    with pytest.raises(InvalidCatalogRecord, match="must not be negative"):
        version(**{field: -1})


@pytest.mark.parametrize("field", ["set_count", "card_count", "external_id_count"])
def test_a_boolean_is_not_a_count(field: str) -> None:
    """`bool` is an `int`, and `True` records are always a bug, never one record."""
    with pytest.raises(InvalidCatalogRecord, match="must be an integer"):
        version(**{field: True})


def test_an_empty_count_is_a_legitimate_record() -> None:
    """A run that imported nothing still happened, and still gets recorded."""
    assert version(set_count=0, card_count=0, external_id_count=0).card_count == 0


def test_an_empty_licence_reference_is_refused() -> None:
    with pytest.raises(InvalidCatalogRecord, match="source_license"):
        version(source_license="")


def test_a_padded_revision_is_refused() -> None:
    with pytest.raises(InvalidCatalogRecord, match="source_revision"):
        version(source_revision=" 8f2c1ab ")


# ---------------------------------------------------------------------------
# Metadata is copied, not borrowed
# ---------------------------------------------------------------------------
def test_metadata_is_copied_so_the_caller_cannot_change_a_validated_record() -> None:
    supplied = {"upstream_repository": "tcgdex/cards-database"}

    record = version(metadata=supplied)
    supplied["upstream_repository"] = "somewhere-else"

    assert record.metadata["upstream_repository"] == "tcgdex/cards-database"


def test_metadata_is_read_only() -> None:
    record = version(metadata={"a": 1})

    with pytest.raises(TypeError):
        record.metadata["a"] = 2  # type: ignore[index]


def test_metadata_keys_must_be_strings() -> None:
    with pytest.raises(InvalidCatalogRecord, match="metadata keys"):
        version(metadata={1: "one"})


def test_every_rejection_is_a_domain_error() -> None:
    """Callers catch the domain with one clause; see `errors.py`."""
    with pytest.raises(DomainError):
        version(version="latest")


# ---------------------------------------------------------------------------
# The port — exercised against an in-memory implementation
# ---------------------------------------------------------------------------
class InMemoryCardDatabaseVersionRepository:
    """Append-only, like the table it stands in for. Order is insertion order."""

    def __init__(self) -> None:
        self._published: list[CardDatabaseVersion] = []

    def publish(self, record: CardDatabaseVersion) -> None:
        if any(existing.version == record.version for existing in self._published):
            return
        self._published.append(record)

    async def current(self) -> CardDatabaseVersion | None:
        return self._published[-1] if self._published else None

    async def get(self, version: str) -> CardDatabaseVersion | None:
        return next((row for row in self._published if row.version == version), None)


@pytest.fixture
def repository() -> InMemoryCardDatabaseVersionRepository:
    return InMemoryCardDatabaseVersionRepository()


def test_the_in_memory_implementation_satisfies_the_port(
    repository: InMemoryCardDatabaseVersionRepository,
) -> None:
    port: CardDatabaseVersionRepository = repository

    assert port is repository


def test_no_version_registered_is_not_an_error(
    repository: InMemoryCardDatabaseVersionRepository,
) -> None:
    """Whether that is a failure depends on who asked, so the port does not decide."""
    assert run(repository.current) is None


def test_the_current_version_is_the_most_recently_published(
    repository: InMemoryCardDatabaseVersionRepository,
) -> None:
    repository.publish(version(version="pokemon-catalog-v0.1.0"))
    repository.publish(version(version="pokemon-catalog-v0.2.0"))

    current = run(repository.current)

    assert current is not None
    assert current.version == "pokemon-catalog-v0.2.0"


def test_publication_order_wins_over_the_timestamp(
    repository: InMemoryCardDatabaseVersionRepository,
) -> None:
    """An import of older data that ran later is still the catalog now in place."""
    repository.publish(version(version="pokemon-catalog-v0.1.0", generated_at=GENERATED_AT))
    repository.publish(
        version(
            version="pokemon-catalog-v0.2.0",
            generated_at=GENERATED_AT - timedelta(days=365),
        )
    )

    current = run(repository.current)

    assert current is not None
    assert current.version == "pokemon-catalog-v0.2.0"


def test_a_historical_version_can_be_looked_up(
    repository: InMemoryCardDatabaseVersionRepository,
) -> None:
    """This is what makes an old analysis re-derivable rather than re-guessable."""
    repository.publish(version(version="pokemon-catalog-v0.1.0"))
    repository.publish(version(version="pokemon-catalog-v0.2.0"))

    historical = run(lambda: repository.get("pokemon-catalog-v0.1.0"))

    assert historical is not None
    assert historical.version == "pokemon-catalog-v0.1.0"


def test_an_unknown_version_is_absent_rather_than_an_error(
    repository: InMemoryCardDatabaseVersionRepository,
) -> None:
    assert run(lambda: repository.get("pokemon-catalog-v9.9.9")) is None
