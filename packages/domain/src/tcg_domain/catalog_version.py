"""Which card catalog an answer was computed against — spec §57.

Spec §57 requires every analysis to record seven things so that it can be
re-derived rather than re-guessed, and one of them is ``card_database_version``.
:class:`CardDatabaseVersion` is that field's type: one import run, stated once
and never restated.

**The identifier is explicit and ordered, never a pointer.** Spec §31 sets the
rule for model bundles — ``pokemon-condition-v0.3.0``, and "a model must never
simply reference ``/latest/``" — and the same rule governs a catalog. A record
that said "latest" would name a moving target, which is the one thing a
reproducibility record may not do, so :data:`VERSION_PATTERN` rejects it here
rather than at whichever edge happens to notice first.

**Provenance travels with the version.**
``docs/adr/0004-the-canonical-card-catalog-source.md`` requires that source,
licence reference, retrieved-at, the upstream commit imported and record counts
are recorded per import run, in one place, with no competing provenance table.
Those are the fields below.

Two omissions, both following choices :mod:`tcg_domain.catalog` already made:

* **No surrogate id.** ``catalog.py`` omits the one on ``card_external_ids``
  because nothing in the domain references such a row by it. Same here: a
  version's identity is its identifier. The table still has a primary key.
* **No ordinal.** Publication order is a database fact, and the port below
  offers no listing, so the order never surfaces in the domain. Leaving it out
  is also what lets a writer construct and validate a record *before* the
  insert that orders it — which is how the seed loader already treats every
  fixture.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Protocol

from tcg_domain.card import validated_identifier, validated_slug
from tcg_domain.errors import InvalidCatalogRecord

__all__ = ["VERSION_PATTERN", "CardDatabaseVersion", "CardDatabaseVersionRepository"]

#: The shape of a catalog version identifier: a lowercase slug naming what was
#: imported, then an explicit semantic version — ``pokemon-catalog-v0.3.0``.
VERSION_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-v\d+\.\d+\.\d+$")

_NO_METADATA: Final[Mapping[str, object]] = MappingProxyType({})


def _validated_version(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidCatalogRecord(f"version must be a string, got {type(value).__name__}")
    # Checked before the pattern so the message names the actual mistake. A
    # moving pointer is not a malformed identifier, it is the wrong idea.
    if "latest" in value:
        raise InvalidCatalogRecord(
            f"version must name one immutable catalog, not a moving pointer: {value!r}. "
            "Spec §31: a version is explicit and ordered, such as 'pokemon-catalog-v0.3.0'."
        )
    if not VERSION_PATTERN.match(value):
        raise InvalidCatalogRecord(
            f"version must look like 'pokemon-catalog-v0.3.0', got {value!r}"
        )
    return str(value)


def _validated_generated_at(value: object) -> datetime:
    # A naive timestamp means "some o'clock, somewhere", which is not a fact a
    # reproducibility record can carry. `date` is refused by the isinstance
    # check; `datetime` without a tzinfo is refused explicitly, because it is
    # the mistake that otherwise passes.
    if not isinstance(value, datetime):
        raise InvalidCatalogRecord(f"generated_at must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise InvalidCatalogRecord(f"generated_at must carry a timezone, got {value!r}")
    return value


def _validated_count(field: str, value: object) -> int:
    # `bool` is an `int`, and `True` as a record count is always a bug rather
    # than a count of one.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidCatalogRecord(f"{field} must be an integer, got {type(value).__name__}")
    if value < 0:
        raise InvalidCatalogRecord(f"{field} must not be negative, got {value}")
    return value


def _validated_metadata(value: object) -> Mapping[str, object]:
    """Copy the caller's mapping behind a read-only view, as `catalog.py` does."""
    if not isinstance(value, Mapping):
        raise InvalidCatalogRecord(f"metadata must be a mapping, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise InvalidCatalogRecord(f"metadata keys must be strings, got {type(key).__name__}")
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class CardDatabaseVersion:
    """One import run: what was imported, from where, when, and how much of it.

    Args:
        version: An explicit, ordered identifier — ``pokemon-catalog-v0.3.0``.
            Never a moving pointer; see spec §31.
        source: A lowercase slug naming where the data came from — ``tcgdex``
            for the canonical source, ``manual`` for the hand-authored fixtures.
        generated_at: When the source data was produced or retrieved, with a
            timezone. Distinct from when the row was written, which is the
            table's business.
        set_count: How many sets the run wrote.
        card_count: How many cards the run wrote.
        external_id_count: How many provider identifiers the run wrote.
        source_license: The licence the source data arrived under — ``MIT`` for
            TCGdex, per ADR 0004. `None` where the data is this project's own
            and no upstream licence applies.
        source_revision: The upstream revision imported, where the source has
            one, so the import can be repeated exactly.
        metadata: Whatever the source carried that has no field of its own.

    Raises:
        InvalidCatalogRecord: If any field is empty or malformed.
    """

    version: str
    source: str
    generated_at: datetime
    set_count: int
    card_count: int
    external_id_count: int
    source_license: str | None = None
    source_revision: str | None = None
    metadata: Mapping[str, object] = _NO_METADATA

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        set_field(self, "version", _validated_version(self.version))
        set_field(self, "source", validated_slug("source", self.source, error=InvalidCatalogRecord))
        set_field(self, "generated_at", _validated_generated_at(self.generated_at))
        set_field(self, "set_count", _validated_count("set_count", self.set_count))
        set_field(self, "card_count", _validated_count("card_count", self.card_count))
        set_field(
            self,
            "external_id_count",
            _validated_count("external_id_count", self.external_id_count),
        )
        if self.source_license is not None:
            set_field(
                self,
                "source_license",
                validated_identifier(
                    "source_license", self.source_license, error=InvalidCatalogRecord
                ),
            )
        if self.source_revision is not None:
            set_field(
                self,
                "source_revision",
                validated_identifier(
                    "source_revision", self.source_revision, error=InvalidCatalogRecord
                ),
            )
        set_field(self, "metadata", _validated_metadata(self.metadata))

    def __str__(self) -> str:
        return self.version


class CardDatabaseVersionRepository(Protocol):
    """What the application may ask about catalog versions.

    Read-only, on the same terms as
    :class:`tcg_domain.repository.CardRepository`: V1's only writers are the
    seed loader and the import pipeline, each of which owns the transaction
    that writes the catalog the version describes and so writes the version in
    it rather than through here.

    Absence returns `None` rather than raising, because whether "no version has
    been registered" is an error depends entirely on who asked.
    """

    async def current(self) -> CardDatabaseVersion | None:
        """The most recently published version, or None if there is none.

        "Most recent" is publication order, not :attr:`generated_at`: an import
        of older data that ran later is still the catalog now in place.

        Raises:
            CatalogUnavailable: If the catalog could not be reached.
        """

    async def get(self, version: str) -> CardDatabaseVersion | None:
        """The version with this identifier, or None if it was never published.

        Args:
            version: The identifier recorded by an earlier analysis.

        Raises:
            CatalogUnavailable: If the catalog could not be reached.
        """
