"""The market-data port — what the application may ask of a price provider.

Spec §33 names three methods and one rule: "the core application only depends
on this interface". Spec §2.4 says why: "no marketplace, price provider, card
database, or external API may become a hard dependency of the core domain". So
there is deliberately no ``api_key``, ``base_url``, ``sku``, ``page_token`` or
HTTP type anywhere below — those are an adapter's private business, exactly as
:mod:`tcg_shared.storage.port` refuses to name a bucket and
:mod:`tcg_domain.repository` refuses to name a connection string. ADR 0006
selects a vendor for V1 and says the same thing in the other direction: it
"enters the system behind §33's ``MarketDataProvider`` and nothing more".

**The central shape decision is that an absent price is a return value.**
Spec §38 requires the product to say "market price unavailable", and §2.7 makes
uncertainty a legitimate output rather than a failure. A price of zero is a
*valid observation* for a worthless card, so an implementation that signalled
absence with ``Money.zero()`` would feed a real number into
``EV = Σ P(g)·V(g)`` and produce a confident, wrong recommendation. Both price
methods therefore return :data:`~tcg_domain.confidence.Uncertain`: a
:class:`PriceObservation`, or an
:class:`~tcg_domain.confidence.InsufficientInformation` that is falsy, carries a
reason and must never be raised. ADR 0006 already uses that word for the case
that forced it — the V1 provider does not cover TAG at all, and the answer is
``insufficient_information``, never a substituted or interpolated PSA price.

That is not the same as spec §66's ``market_data_unavailable``. Whether an
absent price is fatal to a recommendation, a 503, or an honest
``insufficient_information`` in a 200 body depends on what the caller needed it
for, which this module does not know. #55 and #56 make that mapping; the port
keeps all three readings open. A provider that *fails* is the other thing
entirely, and raises :class:`~tcg_market_data.errors.MarketProviderUnavailable`.

Every method is `async`, following :class:`tcg_domain.repository.CardRepository`
and :class:`tcg_shared.storage.port.ObjectStorage` rather than
:class:`tcg_grading_companies.port.GradingCompanyAdapter`. The two precedents
split on whether the port reaches a network: the grading port reaches nothing
and says so, this one reaches an HTTP API, and ``GET /cards/{id}/market`` (#56)
sits on the API's event loop where a blocking call is an outage under load.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from tcg_domain.card import CardReference, validated_slug
from tcg_domain.confidence import Confidence, Uncertain
from tcg_domain.grade import Grade
from tcg_domain.money import Money
from tcg_grading_companies.companies import ADAPTERS
from tcg_grading_companies.errors import UnsupportedGrade

from tcg_market_data.errors import InvalidMarketObservation

__all__ = [
    "MarketDataProvider",
    "MarketType",
    "PriceObservation",
    "validated_grade_key",
]


class MarketType(StrEnum):
    """Spec §35's ``market_type``: what kind of price was observed.

    Closed, unlike :class:`tcg_grading_companies.port.GradingCompany`, and for
    :class:`tcg_domain.analysis.AnalysisStatus`'s reason: a third kind of market
    price is not a row of data, it is a branch nothing has written. §35 names
    exactly these two, and #50 builds its CHECK constraint from this enum rather
    than retyping the strings.

    It is never stored on an observation — see
    :attr:`PriceObservation.market_type`.
    """

    RAW = "raw"
    GRADED = "graded"


def validated_grade_key(company: str, grade: Grade) -> str:
    """Refuse a grade the named company cannot issue, and return its slug.

    Spec §35 keys a graded price by ``(grading_company, grade)``, and the three
    V1 companies do not share a scale — PSA and TAG issue no 9.5 and BGS does,
    while all three issue half grades elsewhere. A ``psa`` observation at 9.5 is
    therefore not a price at all, and storing one would put a grade in the
    database that no submission can ever return.

    :meth:`~tcg_grading_companies.scale.GradeScale.supports` is the check rather
    than plain membership, so spec §24's collapsed tails follow the same rule:
    ``9.5_or_higher`` is legal for BGS and illegal for PSA without a second rule
    saying so.

    Exported rather than private because two callers want it: #52's adapter,
    which checks a key before spending an HTTP request on it, and
    :class:`~tcg_market_data.memory.InMemoryMarketDataProvider`. Calling it from
    :meth:`PriceObservation.__post_init__` is what makes every implementation —
    the fake, #52's adapter, #51's snapshot rehydration — get the check for no
    code at all.

    Args:
        company: The company's lowercase slug.
        grade: The grade being claimed for it.

    Returns:
        The validated company slug.

    Raises:
        InvalidMarketObservation: If `grade` is not a :class:`~tcg_domain.grade.Grade`,
            or `company` is not a lowercase slug.
        UnsupportedGrade: If an adapter exists for `company` and its scale does
            not support `grade`.
    """
    if not isinstance(grade, Grade):
        raise InvalidMarketObservation(
            f"grade must be a Grade, got {type(grade).__name__}; "
            "use Grade.parse() to build one from a provider's string key"
        )
    slug = validated_slug("grading_company", company, error=InvalidMarketObservation)
    adapter = ADAPTERS.get(slug)
    # A company with no adapter is accepted, not refused, and this is not an
    # oversight to tidy up. `GradingCompany` is deliberately a vocabulary rather
    # than a closed enum so that spec §22's "a fourth company costs one new
    # adapter and no caller change" stays true; raising here would quietly make
    # `ADAPTERS` the closed set of valid companies and undo exactly that. The
    # day CGC gets an adapter, its grades start being checked, and nothing else
    # changes.
    if adapter is not None and not adapter.get_grade_scale().supports(grade):
        scale = adapter.get_grade_scale()
        raise UnsupportedGrade(
            f"{slug} does not issue grade {grade}; its scale is "
            f"{', '.join(str(item) for item in scale.ordered)}"
        )
    return slug


@dataclass(frozen=True, slots=True)
class PriceObservation:
    """One price, for one card, seen at one moment — spec §35's row, as a type.

    Args:
        card: The card's *printed* identity. Not a catalog id:
            :attr:`tcg_domain.catalog.Card.reference` already records that
            "analysis, market lookup and the economic engine all speak
            `CardReference`, so none of them acquires a dependency on the
            catalog having been imported". #50's ``market_observations.card_id``
            is resolved by the ingestion worker, which holds the `Card`.
        price: An exact decimal amount and its currency, which together are
            §35's ``price`` and ``currency`` columns. **Zero is a legal
            observation** — a worthless card really is worth nothing — and is
            the case this whole type exists to keep distinct from "we do not
            know". Negative is not.
        observed_at: When the price was seen, timezone-aware. A naive timestamp
            would make #55's ``price_age`` silently wrong and §36's
            reproducibility a claim rather than a fact.
        confidence: How much this single observation is worth — the provider's
            own signal, from sample size and spread. **Not staleness**:
            §38's ``price_age`` is a function of `observed_at` and the moment of
            asking, and #55 computes both it and the composite
            ``price_confidence`` from these inputs.
        provider: The provider's lowercase slug, as
            :attr:`tcg_domain.catalog.CardExternalId.provider` already spells a
            source (``tcgdex``, ``manual``). **Not the display name**: ADR 0006
            binds the string ``PokePriceTracker``, and that belongs in #50's
            ``market_providers.name``, where the licence and commercial-use
            fields live beside it. Putting it here would be refused, and would
            be a second home for one fact.
        grading_company: The company that graded the card, for a graded price.
            `None` for a raw one. Typed `str`, not
            :class:`~tcg_grading_companies.port.GradingCompany`, because #46
            fixed that vocabulary as open.
        grade: The grade it was graded at, for a graded price. `None` for a raw
            one.

    Raises:
        InvalidMarketObservation: If the timestamp is naive, the price is
            negative, the provider is not a slug, or the record is half-graded —
            carrying one of `grading_company` and `grade` without the other.
        UnsupportedGrade: If `grade` is not on `grading_company`'s scale.
    """

    card: CardReference
    price: Money
    observed_at: datetime
    confidence: Confidence
    provider: str
    grading_company: str | None = None
    grade: Grade | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.price, Money):
            raise InvalidMarketObservation(
                f"price must be a Money, got {type(self.price).__name__}"
            )
        if not isinstance(self.confidence, Confidence):
            raise InvalidMarketObservation(
                f"confidence must be a Confidence, got {type(self.confidence).__name__}"
            )
        if not isinstance(self.card, CardReference):
            raise InvalidMarketObservation(
                f"card must be a CardReference, got {type(self.card).__name__}"
            )
        if not isinstance(self.observed_at, datetime) or self.observed_at.utcoffset() is None:
            raise InvalidMarketObservation(
                f"observed_at must be a timezone-aware datetime, got {self.observed_at!r}"
            )
        # Zero is deliberately allowed: it is a real observation about a card
        # nobody will pay for, and the one value that must never be confused
        # with an absent price. Negative is not a price anybody ever saw.
        if self.price.amount < 0:
            raise InvalidMarketObservation(f"a price cannot be negative, got {self.price}")
        object.__setattr__(
            self,
            "provider",
            validated_slug("provider", self.provider, error=InvalidMarketObservation),
        )
        if (self.grading_company is None) != (self.grade is None):
            raise InvalidMarketObservation(
                "a graded observation needs both a grading company and a grade, and a raw "
                f"one needs neither; got company={self.grading_company!r}, grade={self.grade!r}"
            )
        if self.grading_company is not None and self.grade is not None:
            object.__setattr__(
                self, "grading_company", validated_grade_key(self.grading_company, self.grade)
            )

    @property
    def market_type(self) -> MarketType:
        """Spec §35's ``market_type``, derived rather than stored.

        Two fields that must agree is precisely the drift #50 has to police in
        SQL; deriving it makes a record claiming ``raw`` while carrying a
        grading company unrepresentable one layer earlier.
        """
        return MarketType.RAW if self.grading_company is None else MarketType.GRADED

    @property
    def history_key(self) -> tuple[datetime, str, Decimal, int]:
        """The total order :meth:`MarketDataProvider.get_price_history` promises.

        Ascending `observed_at` first, because that is what a history is. The
        remaining three break ties, and being total is the point rather than a
        nicety: #51 resolves a snapshot as "the observations as of a moment",
        and two rows that tie under a partial key could come back in either
        order, which would make an immutable snapshot resolve differently on
        two readings of the same data.

        `market_type` is not in the key — the empty company slug that a raw
        observation contributes already sorts every raw row ahead of every
        graded one for the same instant.
        """
        grade = self.grade
        return (
            self.observed_at,
            self.grading_company or "",
            grade.value if grade is not None else Decimal(0),
            grade.bound.sort_offset if grade is not None else 0,
        )

    def __str__(self) -> str:
        graded = "" if self.grading_company is None else f" {self.grading_company} {self.grade}"
        return f"{self.card}{graded}: {self.price} @ {self.observed_at.isoformat()}"


class MarketDataProvider(Protocol):
    """Somewhere card prices can be read from — spec §33's three methods.

    Implementations must raise only :mod:`tcg_market_data.errors` types, so
    swapping one for another changes no caller's error handling and no vendor
    exception reaches a route handler or an ingestion run.

    Three methods, because §33 names three. There is no batch fetch, no
    pagination, no ``refresh`` and no credential: #52 will want batching once it
    has a real quota to fit inside, and adding it then is cheaper than guessing
    the shape now.
    """

    @property
    def provider(self) -> str:
        """The provider's lowercase slug — what every observation is stamped with."""

    async def get_raw_price(self, card: CardReference) -> Uncertain[PriceObservation]:
        """The current ungraded market price for `card`.

        Returns:
            A :class:`PriceObservation` whose `grading_company` and `grade` are
            both `None`, or
            :class:`~tcg_domain.confidence.InsufficientInformation` when the
            provider has no price for this card. **A price of zero is the
            first of those, never the second.**

        Raises:
            MarketProviderUnavailable: If the provider could not be reached.
        """

    async def get_graded_price(
        self, card: CardReference, company: str, grade: Grade
    ) -> Uncertain[PriceObservation]:
        """The current market price for `card` graded `grade` by `company`.

        Args:
            card: The card's printed identity.
            company: The grading company's lowercase slug.
            grade: A grade on that company's scale.

        Returns:
            A :class:`PriceObservation` carrying both `company` and `grade`, or
            :class:`~tcg_domain.confidence.InsufficientInformation` when the
            provider prices no such combination. ADR 0006 makes that the answer
            for every TAG figure in V1 — **never a substituted PSA price and
            never an interpolated one**, which would be fabricated certainty in
            the one place a user is deciding where to spend money.

        Raises:
            UnsupportedGrade: If `grade` is not on `company`'s scale. Asking a
                company for a grade it cannot issue is a bug in the caller, not
                a gap in the data, so it is refused rather than answered
                "unavailable".
            InvalidMarketObservation: If `company` is not a lowercase slug.
            MarketProviderUnavailable: If the provider could not be reached.
        """

    async def get_price_history(self, card: CardReference) -> tuple[PriceObservation, ...]:
        """Every observation the provider holds for `card`, oldest first.

        Raw and graded observations together, in one sequence; a graded one is
        identified by its `grading_company`. Ordered by
        :attr:`PriceObservation.history_key`, which is total — see there for why
        that matters to #51.

        An empty tuple means the provider holds no history, which is an answer
        rather than a failure: the reading
        :meth:`tcg_domain.repository.CardRepository.external_ids` already takes,
        and the reason this method is not wrapped in
        :data:`~tcg_domain.confidence.Uncertain` when the price methods are.

        Raises:
            MarketProviderUnavailable: If the provider could not be reached.
        """
