"""The economics of one analysis — spec §64's configuration and results contract.

Two routes, and between them the whole of what M5 says to a client:

```text
POST /analyses/{id}/economic-configuration
GET  /analyses/{id}/results
```

**The two profit figures are never conflated, and this module is where that
promise is kept or broken.** Spec §41 defines both and says the distinction
"must be implemented rather than conflated"; `packages/economic-engine` keeps
them apart by giving them no shared field name, and a wire format with a single
`expected_profit` would undo that in the one place a user actually reads. So the
response names `incremental_grading_decision` and `investment_return`
separately, and `incremental_roi` and `investment_roi` separately, and
`test_results_endpoint.py` asserts over the models' own fields that no name is
shared and that nothing is called `roi` alone.

**Nothing here calculates anything.** The engine is imported for its types and
its validation, never for a formula written out again — the issue's own non-goal,
and the master architectural rule one layer up. What this module does is parse,
store, read back and lay out.

**Amounts are decimal strings and `currency` is stated once.** A JSON number is
a float in most clients, and a rounding error in a figure somebody is deciding
money on is not acceptable — the same argument `GET /cards/{id}/market` makes for
`PriceResponse.amount`. Ratios are four-place strings, because ADR 0007 fixes
that and `Money`'s two places are for money.

**An absent figure is present-and-null beside its own reason**, never omitted
and never zero. That is #56's rule for a missing price, #91's for an unmeasured
confidence, and ADR 0007's for an absent acquisition cost, which reaches a client
here as `investment_roi_reason: "acquisition_cost_not_supplied"`.

**Results fill in from what the worker stored, never from a prediction made
here.** `_load_predictions` reads #227's `analyses.grade_predictions` document,
filtered to the configuration's companies, and prices each distribution against
the snapshot the analysis recorded — never a provider (ADR 0006), never today's
cut; the engine does the rest. `companies` is `[]` and `recommendation` is
`null` until an analysis has both a configuration and a stored prediction, and
that `null` still means nobody has asked — a third thing from §44's
`insufficient_information`, which is the engine having been asked and declined.
A company whose model refused cannot be a `companies` entry, because it has no
distribution to carry; it is in `refused` with its stored reason, and in the
comparison's `unranked` beside the engine's own admissions whenever there is a
comparison to carry it (#238). The alternative to the empty answer was a 409
until `completed`; an empty result that names the analysis's own status tells a
client more.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Final
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, BeforeValidator, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import AnalysisStatus
from tcg_domain.catalog import CardId
from tcg_domain.confidence import Confidence, InsufficientInformation
from tcg_domain.distribution import GradeDistribution
from tcg_domain.errors import CatalogUnavailable, InvalidMoney
from tcg_domain.grade import Grade
from tcg_domain.money import Money
from tcg_economic_engine import (
    CompanyComparison,
    CompanyOutlook,
    CostConfiguration,
    EconomicEngineError,
    GradedPrice,
    Recommendation,
    SellingFee,
    company_outlook,
    recommend,
    strategy_for,
)
from tcg_grading_companies.companies import ADAPTERS
from tcg_market_data import MarketSnapshot, PriceObservation, price_confidence

from tcg_api.analysis.images import ImageQuality, read_quality
from tcg_api.analysis.sessions import (
    AnalysisRecord,
    AnalysisStoreUnavailable,
    read_analysis,
    read_grade_predictions,
    resolve_session,
)
from tcg_api.catalog.cards import PostgresCardRepository
from tcg_api.config import Settings, get_settings
from tcg_api.economics.store import (
    EconomicConfiguration,
    EconomicConfigurationUnavailable,
    create_configuration,
    read_configuration,
)
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.market.snapshots import MarketSnapshotUnavailable, get_snapshot, resolve_prices
from tcg_api.rate_limit import analysis_rate_limit
from tcg_api.routers.analyses import SESSION_COOKIE, analysis_session

logger = structlog.get_logger(__name__)

__all__ = [
    "CompanyEconomicsResponse",
    "EconomicConfigurationRequest",
    "EconomicConfigurationResponse",
    "RecommendationResponse",
    "ResultsResponse",
    "router",
]

# The `/analyses` prefix is shared with `routers/analyses.py`, which owns the
# lifecycle. Two routers, one prefix: these are different paths, so neither
# shadows the other whatever order they mount in — `cards.py` and `market.py`
# already share `/cards` on the same terms.
router = APIRouter(prefix="/analyses", tags=["economics"])

#: One message for four different misses, exactly as `routers/analyses.py`
#: answers them: unknown analysis, another session's, no cookie, expired cookie.
_NOT_FOUND: Final = "No analysis is recorded under that identifier."

_UNREACHABLE: Final = "The analysis store could not be reached."
_CONFIGURATION_UNREACHABLE: Final = "The economic configuration could not be stored."
_MARKET_UNREACHABLE: Final = "The market data store could not be reached."
_CATALOG_UNREACHABLE: Final = "The card catalog could not be reached."

#: The one state a configuration may be recorded from. Spec §5's journey puts
#: *Economic Configuration* immediately after *User Confirmation*, and §65 gives
#: that step of the pipeline a state: `POST /analyses/{id}/confirm-card` is what
#: puts an analysis here.
_CONFIGURABLE: Final = AnalysisStatus.ANALYZING

_ALREADY_CONFIGURED: Final = (
    "This analysis already has an economic configuration, and a configuration is "
    "immutable. Start a new analysis to price the card differently."
)

#: `no-store` for `GET /cards/{id}/market`'s reason: every figure a result
#: carries is derived from prices whose confidence is discounted for age at the
#: moment of asking, so a cached body would report a frozen one.
_CACHE_CONTROL: Final = "no-store"


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
# Validation is the **engine's**, called from pydantic validators, so there is
# one definition of "a rate is a proportion" and "an amount is not negative"
# rather than one here and one in `costs.py` free to disagree. A refusal comes
# back as FastAPI's own 422: `errors.py` is explicit that request-validation
# responses stay outside spec §66's taxonomy, which has no code meaning "you
# sent something malformed".


def _decimal_string(value: object) -> object:
    """Refuse a JSON number where an exact amount is meant.

    Accepting `40.1` would take a binary float through `Decimal`, which is the
    rounding this repository serialises money as a string to avoid. Refusing it
    at the door means the exactness holds in both directions rather than only
    outbound.
    """
    if isinstance(value, str):
        return value
    raise ValueError(
        "must be a decimal string such as '40.00' — a JSON number is a binary "
        "float in most clients, and money must stay exact"
    )


def _money(value: Decimal) -> Decimal:
    """Run one amount through `Money`, which is what rejects a bad one."""
    try:
        amount = Money(value)
    except InvalidMoney as error:
        raise ValueError(str(error)) from error
    if amount.amount < 0:
        raise ValueError("must not be negative")
    return amount.amount


#: An amount, as the engine would accept it: a decimal string, quantised to the
#: cent by `Money` and never negative — which is what keeps ADR 0007's claim
#: that neither `CapitalAtRisk` denominator can go below zero true at the door.
Amount = Annotated[Decimal, BeforeValidator(_decimal_string), Field(examples=["40.00"])]

#: #58's own placeholders, read from the engine rather than restated. A second
#: copy here would be a second set of defaults free to drift from the ones every
#: figure is actually computed with — and `apps/web` reads these back off a
#: stored configuration, so this is the *only* place they are written down.
DEFAULT_COSTS: Final = CostConfiguration()


class SellingFeeRequest(BaseModel):
    """Spec §46's `selling_fee`: a proportion of the sale price, plus a flat part."""

    rate: Amount = Field(
        default_factory=lambda: DEFAULT_COSTS.selling_fee.rate,
        description=(
            "The proportion of the realised sale price taken as commission. **A "
            'proportion in [0, 1], never a percentage**: ten percent is `"0.10"`, and '
            '`"10"` is refused rather than silently read as 1000%.'
        ),
        examples=["0.10"],
    )
    flat: Amount = Field(
        default_factory=lambda: DEFAULT_COSTS.selling_fee.flat.amount,
        description="The fixed part, charged per sale regardless of price.",
    )

    @field_validator("rate")
    @classmethod
    def _validate_rate(cls, value: Decimal) -> Decimal:
        try:
            SellingFee(rate=value)
        except EconomicEngineError as error:
            raise ValueError(str(error)) from error
        return value

    @field_validator("flat")
    @classmethod
    def _validate_flat(cls, value: Decimal) -> Decimal:
        return _money(value)


class CostConfigurationRequest(BaseModel):
    """Spec §46's six line items. **Never a total** — #58 binds that there is none.

    Every field has the engine's own default, so a client that has nothing to say
    about shipping does not have to invent a number, and the defaults live in one
    place rather than being restated in `apps/web`. They are illustrative
    placeholders and deliberately non-zero: an all-zero configuration reports
    grading as costless and tilts every recommendation toward *grade*.
    """

    grading_fee: Amount = Field(default_factory=lambda: DEFAULT_COSTS.grading_fee.amount)
    outbound_shipping: Amount = Field(
        default_factory=lambda: DEFAULT_COSTS.outbound_shipping.amount
    )
    return_shipping: Amount = Field(default_factory=lambda: DEFAULT_COSTS.return_shipping.amount)
    insurance: Amount = Field(default_factory=lambda: DEFAULT_COSTS.insurance.amount)
    miscellaneous: Amount = Field(default_factory=lambda: DEFAULT_COSTS.miscellaneous.amount)
    selling_fee: SellingFeeRequest = Field(default_factory=SellingFeeRequest)

    @field_validator(
        "grading_fee",
        "outbound_shipping",
        "return_shipping",
        "insurance",
        "miscellaneous",
    )
    @classmethod
    def _validate_amount(cls, value: Decimal) -> Decimal:
        return _money(value)

    def to_domain(self) -> CostConfiguration:
        """Build the engine's own frozen configuration. Every field is already valid."""
        return CostConfiguration(
            grading_fee=Money(self.grading_fee),
            outbound_shipping=Money(self.outbound_shipping),
            return_shipping=Money(self.return_shipping),
            insurance=Money(self.insurance),
            miscellaneous=Money(self.miscellaneous),
            selling_fee=SellingFee(
                rate=self.selling_fee.rate,
                flat=Money(self.selling_fee.flat),
            ),
        )


class EconomicConfigurationRequest(BaseModel):
    """What the user says the economics of their decision are — spec §45, §46, §43."""

    acquisition_cost: Amount | None = Field(
        default=None,
        description=(
            "What the user paid, if they said. **Absent is not zero**: `null` means "
            "they did not say and is reported as `acquisition_cost_not_supplied`, "
            'while `"0.00"` is a real acquisition cost — a raffle win, a pull from '
            "somebody else's pack. Spec §45 forbids inferring it, so nothing here "
            "fills it in from the market price."
        ),
        examples=["120.00"],
    )
    costs: CostConfigurationRequest = Field(default_factory=CostConfigurationRequest)
    grading_companies: list[str] = Field(
        min_length=1,
        description=(
            "Which companies to compare, as the slugs `GET /grading-companies` uses. "
            "At least one, and no duplicates — two entries for one company would list "
            "it twice and make 'best' meaningless."
        ),
        examples=[["psa", "bgs"]],
    )
    optimization_mode: str = Field(
        description=(
            "Spec §43's optimization mode: `expected_profit`, `roi`, "
            "`highest_grade_probability`, `lowest_total_cost` or "
            "`expected_graded_value`. **`roi` is a mode name, never a figure** — the "
            "results name two ratios and neither is called `roi`."
        ),
        examples=["expected_profit"],
    )

    @field_validator("acquisition_cost")
    @classmethod
    def _validate_acquisition_cost(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _money(value)

    @field_validator("grading_companies")
    @classmethod
    def _validate_companies(cls, value: list[str]) -> list[str]:
        unknown = [company for company in value if company not in ADAPTERS]
        if unknown:
            supported = ", ".join(sorted(ADAPTERS))
            raise ValueError(f"unsupported grading companies: {unknown}. Supported: {supported}")
        if len(set(value)) != len(value):
            raise ValueError("each grading company may be named at most once")
        return value

    @field_validator("optimization_mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        try:
            strategy_for(value)
        except EconomicEngineError as error:
            raise ValueError(str(error)) from error
        return value


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class SellingFeeResponse(BaseModel):
    rate: str = Field(
        description="A proportion in [0, 1], as a decimal string.", examples=["0.1000"]
    )
    flat: str = Field(description="The fixed part, per sale.", examples=["0.00"])


class CostConfigurationResponse(BaseModel):
    """Spec §46's line items as stored. **There is no total, by design.**

    §47's future dimensions — country, tax, service tier, shipping provider —
    attach to individual lines, so a total is a figure that would have to be
    unpicked again. A client that wants one adds five numbers and knows which
    five it added; the selling fee is not one of them, because ADR 0007 nets it
    out of proceeds rather than committing it up front.
    """

    grading_fee: str
    outbound_shipping: str
    return_shipping: str
    insurance: str
    miscellaneous: str
    selling_fee: SellingFeeResponse


class RecommendationThresholdsResponse(BaseModel):
    """Where the answer changes — #64's five gates, as they stood for this analysis.

    Reported rather than accepted: they are policy, not a card's costs. They are
    stored per configuration so a recommendation stays reproducible when M7/M8's
    calibration moves them, and they are shown so a user can see that
    "insufficient information" was a threshold being missed rather than an
    opinion.
    """

    minimum_image_quality: float = Field(ge=0.0, le=1.0)
    minimum_grade_confidence: float = Field(ge=0.0, le=1.0)
    minimum_figure_confidence: float = Field(ge=0.0, le=1.0)
    maximum_unpriced_probability: float = Field(ge=0.0, le=1.0)
    minimum_incremental_profit: str = Field(examples=["5.00"])


class EconomicConfigurationResponse(BaseModel):
    """One stored configuration, read back exactly as it was written."""

    id: UUID = Field(
        description=(
            "Spec §57's `economic_configuration`. Immutable: an analysis references "
            "this identifier for as long as it exists, and pricing the card "
            "differently is a new analysis rather than an edit."
        ),
    )
    created_at: datetime
    currency: str = Field(
        description="ISO 4217 code for every amount in this object.",
        examples=["SGD"],
    )
    acquisition_cost: str | None = Field(
        description=(
            "What the user paid, or `null` if they did not say. **`null` is not "
            '`"0.00"`** — the second is a real acquisition cost, and the two reach '
            "different §41 answers."
        ),
    )
    costs: CostConfigurationResponse
    grading_companies: list[str]
    optimization_mode: str
    thresholds: RecommendationThresholdsResponse


class GradeProbabilityResponse(BaseModel):
    """One term of a grade distribution — spec §2.1's `P(g)`."""

    grade: str = Field(
        description=(
            "A grade key, spelled as `GET /grading-companies` spells it. A collapsed "
            "tail such as `7_or_lower` is a grade key too."
        ),
        examples=["10"],
    )
    probability: float = Field(ge=0.0, le=1.0)


class ExpectedValueResponse(BaseModel):
    """Spec §40's expectation, and what it could not see."""

    amount: str = Field(
        description=(
            "The expectation **conditional on a priced grade occurring**: an unpriced "
            "grade is excluded and the rest renormalised, never valued at zero."
        ),
        examples=["234.00"],
    )
    confidence: float = Field(ge=0.0, le=1.0)
    unpriced_grades: list[str] = Field(
        description="Which grades the snapshot held no price for. Empty is the good case.",
    )
    unpriced_probability: float = Field(
        ge=0.0,
        le=1.0,
        description="How much of the distribution those grades carried.",
    )


class IncrementalGradingDecisionResponse(BaseModel):
    """Spec §41's first figure: **should I grade the card I already own?**

    The acquisition cost is a sunk cost here and cannot reach this figure — there
    is no field for it, which is how the engine keeps it out and how this keeps
    it out. Compare `InvestmentReturnResponse`, which answers a different
    question with different numbers; the two share no field name on purpose.
    """

    incremental_profit: str = Field(
        description=(
            "Expected graded proceeds, less the raw-sale opportunity value, less "
            "grading costs. **A negative figure is an answer**, not an error: it means "
            "selling the card raw is the better move."
        ),
        examples=["24.00"],
    )
    confidence: float = Field(ge=0.0, le=1.0)
    graded_proceeds: str = Field(
        description="Sum over grades of P(g)*(V(g) less the selling fee on V(g)), the fee applied per outcome.",
    )
    raw_market_value: str = Field(description="What the card fetches ungraded, gross.")
    raw_selling_fee: str = Field(description="What selling it raw would cost.")
    raw_opportunity_value: str = Field(
        description=(
            "The raw sale, net of its own selling fee. **Both branches pay the fee** — "
            "charging it only to the graded side is a systematic bias toward grading."
        ),
    )
    grading_costs: str = Field(
        description=(
            "Five of spec §46's six line items. The selling fee is deliberately not "
            "among them: ADR 0007 nets it out of proceeds rather than counting it as "
            "capital committed up front."
        ),
    )
    unpriced_grades: list[str]
    unpriced_probability: float = Field(ge=0.0, le=1.0)


class InvestmentReturnResponse(BaseModel):
    """Spec §41's second figure: **did buying this card to grade make money?**

    Answerable only when the user said what they paid. Shares no field name with
    the incremental decision, so no client can render one under the other's
    label.
    """

    investment_profit: str = Field(
        description="Expected graded proceeds, less the acquisition cost, less grading costs.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    graded_proceeds: str
    acquisition_cost: str = Field(description="What the user said they paid.")
    grading_costs: str
    unpriced_grades: list[str]
    unpriced_probability: float = Field(ge=0.0, le=1.0)


class RatioResponse(BaseModel):
    """One of ADR 0007's two ratios. **Neither is ever called `roi` alone.**"""

    value: str = Field(
        description=(
            'A ratio quantised to **four** places, as a decimal string. `"0.6250"` is '
            "62.5%. Four rather than money's two because a ratio is not money, and a "
            "string for the same reason an amount is one."
        ),
        examples=["0.6250"],
    )
    capital_at_risk: str = Field(
        description=(
            "The denominator. **It includes the card**, which is why this number is "
            "smaller than figures quoted elsewhere: the numerator has already "
            "subtracted the raw-sale opportunity value, so a denominator omitting it "
            "would pretend the card is not committed. See ADR 0007."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    label: str = Field(
        description="What to call this ratio on screen, from ADR 0007.",
        examples=["Return on grading"],
    )


class CompanyEconomicsResponse(BaseModel):
    """Every M5 figure for one grading company.

    Four figures, four reasons. Each figure is `null` when it could not be
    computed and its reason says which question could not be asked —
    `no_raw_price_available`, `no_graded_price_available`,
    `acquisition_cost_not_supplied`, `no_capital_at_risk`. Present-and-null
    beside a reason, never omitted, and never zero.
    """

    company: str = Field(examples=["psa"])
    grade_distribution: list[GradeProbabilityResponse] = Field(
        description=(
            "**The full distribution, always** — spec §2.1 retains it even when a UI "
            "shows one number. Ascending by grade."
        ),
    )
    distribution_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="How far this company's model is trusted. Never assumed.",
    )
    costs: CostConfigurationResponse
    expected_graded_value: ExpectedValueResponse | None = Field(
        description=(
            "Spec §43's `expected_graded_value` — ADR 0007's `graded_proceeds`, **net "
            "of the selling fee**, the fee applied inside the sum."
        ),
    )
    expected_graded_value_reason: str | None = Field(
        description="Why there is no expectation, when there is none.",
    )
    incremental_grading_decision: IncrementalGradingDecisionResponse | None
    incremental_reason: str | None
    incremental_roi: RatioResponse | None
    incremental_roi_reason: str | None
    investment_return: InvestmentReturnResponse | None
    investment_reason: str | None
    investment_roi: RatioResponse | None
    investment_roi_reason: str | None = Field(
        description=(
            "`acquisition_cost_not_supplied` when the user did not say what they paid "
            "— ADR 0007's own string, and never a zero standing in for it."
        ),
    )


class ReasonResponse(BaseModel):
    """Why the recommendation is what it is — spec §44's `reason`.

    **Four fields and no sentence.** Spec §50 forbids explanations unrelated to
    model evidence, and a reason that is nothing but the figure, its value and
    the threshold it was measured against cannot be unrelated to the evidence.
    The copy that turns this into English lives in `apps/web`; adding a message
    here would put a second, unverifiable explanation on the wire.
    """

    code: str = Field(
        description="What fired, as a stable machine name. Key your copy off this.",
        examples=["profit_clears_margin"],
    )
    figure: str = Field(
        description="What was measured.",
        examples=["incremental_profit"],
    )
    value: str | None = Field(
        description=(
            "The number measured, as a decimal string. `null` when there was no "
            "number — a propagated admission is the absence of a figure, not a figure "
            "with a bad value."
        ),
    )
    threshold: str | None = Field(description="What it was measured against, on the same terms.")


class RankedCompanyResponse(BaseModel):
    company: str
    value: str = Field(description="The figure this company was ranked on, as a decimal string.")
    confidence: float = Field(ge=0.0, le=1.0)
    figure: str = Field(
        description=(
            "**What was ranked** — `incremental_roi`, `incremental_profit`, "
            "`grading_costs`, `graded_proceeds` or a `P(g)`. Never `roi`: §43's `roi` "
            "is a mode name and no figure carries it, so a comparison cannot be shown "
            "under a label its number does not match."
        ),
        examples=["incremental_profit"],
    )


class UnrankedCompanyResponse(BaseModel):
    company: str
    reason: str = Field(
        description=(
            "Why this company has no place in the order. **It is unranked, not last** — "
            "a sentinel sorted to the bottom would read as 'the worst company', which "
            "is a claim nobody computed."
        ),
    )


class CompanyComparisonResponse(BaseModel):
    """Spec §49's compare table, in the order the chosen mode produced."""

    mode: str
    label: str
    ranked: list[RankedCompanyResponse]
    unranked: list[UnrankedCompanyResponse]
    tied_at_the_top: list[str] = Field(
        description=(
            "Companies that tied for first. The order among them is alphabetical and "
            "**means nothing** — say so rather than presenting an arbitrary winner."
        ),
    )


class RecommendationResponse(BaseModel):
    """Spec §44's output. The mode picks the company; the economics pick the action."""

    recommended_action: str = Field(
        description="`grade`, `do_not_grade` or `insufficient_information`.",
        examples=["grade"],
    )
    recommended_company: str | None = Field(
        description=(
            "**`null` whenever the action is `insufficient_information`.** Naming a "
            "company beside 'we cannot tell' is exactly the forced recommendation §44 "
            "forbids — a screen shown both renders the company as the answer."
        ),
    )
    reason: ReasonResponse
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The weakest of the confidences that exist — a minimum, never a product.",
    )
    image_quality: float = Field(ge=0.0, le=1.0)
    grade_confidence: float | None = Field(ge=0.0, le=1.0)
    figure_confidence: float | None = Field(ge=0.0, le=1.0)
    failed_gates: list[ReasonResponse] = Field(
        description=(
            "Every gate that failed, not only the decisive one, so a user who fixes "
            "the first is not sent into a second wall nobody mentioned."
        ),
    )
    comparison: CompanyComparisonResponse | None
    comparison_reason: str | None = Field(
        description="`no_company_can_be_ranked` when no company could be ordered at all.",
    )


class MarketSnapshotReference(BaseModel):
    """Which cut of the market these economics were computed against — spec §36."""

    id: UUID
    generated_at: datetime
    data_version: str = Field(
        description=(
            "**Show this beside the figures.** A dated record of a past market is "
            "honest; the same numbers presented as current are not."
        ),
        examples=["2026-08-25"],
    )


class ResultsResponse(BaseModel):
    """The body of `GET /analyses/{analysis_id}/results` — spec §64, §6, §41, §44."""

    analysis_id: UUID
    status: str = Field(
        description=(
            "The analysis's state, so a client can tell 'not finished yet' from 'we "
            "could not tell'. Spec §65's states; poll `GET /analyses/{id}` for it."
        ),
    )
    card_id: UUID | None = Field(description="The confirmed card, or `null` before confirmation.")
    currency: str = Field(description="ISO 4217 code for every amount below.", examples=["SGD"])
    economic_configuration: EconomicConfigurationResponse | None = Field(
        description="What the economics were computed under, or `null` if none was supplied.",
    )
    market_snapshot: MarketSnapshotReference | None = Field(
        description=(
            "The snapshot recorded on this analysis, or `null` when nothing had been "
            "ingested when it ran."
        ),
    )
    companies: list[CompanyEconomicsResponse] = Field(
        description=(
            "One entry per configured company whose model predicted, in the "
            "configuration's order, each with its full distribution. **Empty until "
            "the analysis has an economic configuration and the worker has stored "
            "its grade predictions** — and empty rather than absent so a client "
            "parses the same shape either way. A company whose model refused is not "
            "here: it has no distribution to carry, and is in `refused` with its reason."
        ),
    )
    refused: dict[str, str] = Field(
        description=(
            "Every configured company whose grade prediction refused, keyed by slug, "
            "each with the reason the worker stored — `condition_step_not_run`, or a "
            "model's own refusal. **With `companies`, this is every configured "
            "company.** Empty until the analysis has an economic configuration and "
            "stored predictions, and empty when every model predicted. A refused "
            "company also appears in `recommendation.comparison.unranked` whenever "
            "some other company could be ranked; when none could, `comparison` is "
            "`null` with `no_company_can_be_ranked` and this is where each reason "
            "lives."
        ),
        examples=[{"bgs": "condition_step_not_run"}],
    )
    recommendation: RecommendationResponse | None = Field(
        description=(
            "Spec §44's answer, or `null` when nothing has been asked yet — no "
            "configuration, or no prediction stored. **`null` is not "
            "`insufficient_information`**: the first means nobody has asked, the "
            "second that we asked and the data did not support an answer."
        ),
    )


# ---------------------------------------------------------------------------
# Engine → wire
# ---------------------------------------------------------------------------
# Pure functions over the engine's frozen results, unit-tested against
# hand-built engine objects rather than through a request: the acceptance
# criterion is about the shape, and the shape is here. `read_results` has been
# their runtime caller since #228.


def _amount(value: Money) -> str:
    """One amount, exactly, as two decimal places."""
    return str(value.amount)


def _ratio(value: Decimal) -> str:
    """One ratio, at ADR 0007's four places. `Money`'s two are for money."""
    return str(value)


def _reason_of(value: Any) -> str | None:
    """The reason an admission carries, or `None` if this is an answer."""
    return value.reason if isinstance(value, InsufficientInformation) else None


def _answer(value: Any) -> Any:
    """The answer, or `None` if this is an admission."""
    return None if isinstance(value, InsufficientInformation) else value


def _figure(value: object) -> str:
    """One ranked figure as a string, whatever kind of number it is.

    `RankedCompany.value` is money on three modes, a four-place ratio on one and
    a bare probability on another; `figure` says which. One string field rather
    than a union keeps a client from having to switch on the type to read it.
    """
    if isinstance(value, Money):
        return _amount(value)
    return str(value)


def _costs(costs: CostConfiguration) -> CostConfigurationResponse:
    return CostConfigurationResponse(
        grading_fee=_amount(costs.grading_fee),
        outbound_shipping=_amount(costs.outbound_shipping),
        return_shipping=_amount(costs.return_shipping),
        insurance=_amount(costs.insurance),
        miscellaneous=_amount(costs.miscellaneous),
        selling_fee=SellingFeeResponse(
            rate=str(costs.selling_fee.rate),
            flat=_amount(costs.selling_fee.flat),
        ),
    )


def _configuration(configuration: EconomicConfiguration) -> EconomicConfigurationResponse:
    thresholds = configuration.thresholds
    return EconomicConfigurationResponse(
        id=configuration.id,
        created_at=configuration.created_at,
        currency=configuration.costs.grading_fee.currency.value,
        acquisition_cost=(
            None
            if configuration.acquisition_cost is None
            else _amount(configuration.acquisition_cost)
        ),
        costs=_costs(configuration.costs),
        grading_companies=list(configuration.companies),
        optimization_mode=configuration.optimization_mode,
        thresholds=RecommendationThresholdsResponse(
            minimum_image_quality=thresholds.minimum_image_quality.value,
            minimum_grade_confidence=thresholds.minimum_grade_confidence.value,
            minimum_figure_confidence=thresholds.minimum_figure_confidence.value,
            maximum_unpriced_probability=thresholds.maximum_unpriced_probability,
            minimum_incremental_profit=_amount(thresholds.minimum_incremental_profit),
        ),
    )


def _company_economics(outlook: CompanyOutlook) -> CompanyEconomicsResponse:
    """One company's figures, laid out so the two §41 answers cannot be confused.

    Nothing is recomputed here: every number is read off the outlook, which
    computed each one once. A figure this module derived could disagree with the
    ratio built from it, which is the drift #62 exists to prevent.
    """
    expectation = _answer(outlook.graded_proceeds)
    incremental = _answer(outlook.incremental)
    investment = _answer(outlook.investment)
    incremental_ratio = _answer(outlook.incremental_ratio)
    investment_ratio = _answer(outlook.investment_ratio)

    return CompanyEconomicsResponse(
        company=outlook.company,
        grade_distribution=[
            GradeProbabilityResponse(grade=str(grade), probability=probability)
            for grade, probability in sorted(
                outlook.distribution.probabilities.items(), key=lambda term: term[0].sort_key
            )
        ],
        distribution_confidence=outlook.distribution_confidence.value,
        costs=_costs(outlook.costs),
        expected_graded_value=(
            None
            if expectation is None
            else ExpectedValueResponse(
                amount=_amount(expectation.amount),
                confidence=expectation.confidence.value,
                unpriced_grades=[str(grade) for grade in expectation.unpriced_grades],
                unpriced_probability=expectation.unpriced_probability,
            )
        ),
        expected_graded_value_reason=_reason_of(outlook.graded_proceeds),
        incremental_grading_decision=(
            None
            if incremental is None
            else IncrementalGradingDecisionResponse(
                incremental_profit=_amount(incremental.incremental_profit),
                confidence=incremental.confidence.value,
                graded_proceeds=_amount(incremental.graded_proceeds),
                raw_market_value=_amount(incremental.raw_market_value),
                raw_selling_fee=_amount(incremental.raw_selling_fee),
                raw_opportunity_value=_amount(incremental.raw_opportunity_value),
                grading_costs=_amount(incremental.grading_costs),
                unpriced_grades=[str(grade) for grade in incremental.unpriced_grades],
                unpriced_probability=incremental.unpriced_probability,
            )
        ),
        incremental_reason=_reason_of(outlook.incremental),
        incremental_roi=(
            None
            if incremental_ratio is None
            else RatioResponse(
                value=_ratio(incremental_ratio.incremental_roi),
                capital_at_risk=_amount(incremental_ratio.capital_at_risk),
                confidence=incremental_ratio.confidence.value,
                label=type(incremental_ratio).label,
            )
        ),
        incremental_roi_reason=_reason_of(outlook.incremental_ratio),
        investment_return=(
            None
            if investment is None
            else InvestmentReturnResponse(
                investment_profit=_amount(investment.investment_profit),
                confidence=investment.confidence.value,
                graded_proceeds=_amount(investment.graded_proceeds),
                acquisition_cost=_amount(investment.acquisition_cost),
                grading_costs=_amount(investment.grading_costs),
                unpriced_grades=[str(grade) for grade in investment.unpriced_grades],
                unpriced_probability=investment.unpriced_probability,
            )
        ),
        investment_reason=_reason_of(outlook.investment),
        investment_roi=(
            None
            if investment_ratio is None
            else RatioResponse(
                value=_ratio(investment_ratio.investment_roi),
                capital_at_risk=_amount(investment_ratio.capital_at_risk),
                confidence=investment_ratio.confidence.value,
                label=type(investment_ratio).label,
            )
        ),
        investment_roi_reason=_reason_of(outlook.investment_ratio),
    )


def _reason(reason: Any) -> ReasonResponse:
    def number(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, Money):
            return _amount(value)
        if isinstance(value, Confidence):
            return str(value.value)
        return str(value)

    return ReasonResponse(
        code=reason.code,
        figure=reason.figure,
        value=number(reason.value),
        threshold=number(reason.threshold),
    )


def _comparison(comparison: CompanyComparison) -> CompanyComparisonResponse:
    return CompanyComparisonResponse(
        mode=comparison.mode,
        label=comparison.label,
        ranked=[
            RankedCompanyResponse(
                company=candidate.company,
                value=_figure(candidate.value),
                confidence=candidate.confidence.value,
                figure=candidate.figure,
            )
            for candidate in comparison.ranked
        ],
        unranked=[
            UnrankedCompanyResponse(company=company, reason=str(admission.reason))
            for company, admission in sorted(comparison.unranked.items())
        ],
        tied_at_the_top=list(comparison.tied_at_the_top),
    )


def _recommendation(recommendation: Recommendation) -> RecommendationResponse:
    comparison = _answer(recommendation.comparison)
    return RecommendationResponse(
        recommended_action=str(recommendation.recommended_action),
        recommended_company=recommendation.recommended_company,
        reason=_reason(recommendation.reason),
        confidence=recommendation.confidence.value,
        image_quality=recommendation.image_quality.value,
        grade_confidence=(
            None
            if recommendation.grade_confidence is None
            else recommendation.grade_confidence.value
        ),
        figure_confidence=(
            None
            if recommendation.figure_confidence is None
            else recommendation.figure_confidence.value
        ),
        failed_gates=[_reason(gate) for gate in recommendation.failed_gates],
        comparison=None if comparison is None else _comparison(comparison),
        comparison_reason=_reason_of(recommendation.comparison),
    )


# ---------------------------------------------------------------------------
# Stored document → engine
# ---------------------------------------------------------------------------
# Pure over #227's document, a configuration and a snapshot's observations, so
# the rules — filtered on read, never re-predicted, a refusal kept apart, a
# missing price left missing — are tested without a database.


def _graded_prices(
    observations: tuple[PriceObservation, ...],
    company: str,
    *,
    at: datetime,
    stale_after: timedelta,
) -> tuple[dict[Grade, GradedPrice], GradedPrice | None]:
    """One company's ladder and the raw price, as the engine takes them.

    `GET /cards/{id}/market`'s keying, one company at a time; the raw price is
    the same for every company. The confidence is #55's, discounted for age at
    the moment of asking — which is what makes this route's `no-store` true
    rather than cautious.
    """

    def priced(observation: PriceObservation) -> GradedPrice:
        return GradedPrice(
            observation.price,
            price_confidence(observation, at=at, stale_after=stale_after),
        )

    graded = {
        observation.grade: priced(observation)
        for observation in observations
        if observation.grading_company == company and observation.grade is not None
    }
    raw = next(
        (
            priced(observation)
            for observation in observations
            if observation.grading_company is None
        ),
        None,
    )
    return graded, raw


def _outlooks(
    document: Mapping[str, Any],
    configuration: EconomicConfiguration,
    observations: tuple[PriceObservation, ...],
    *,
    at: datetime,
    stale_after: timedelta,
) -> tuple[tuple[CompanyOutlook, ...], dict[str, InsufficientInformation]]:
    """#227's document, filtered to the configured companies on read.

    Never re-predicted: an entry is what the worker stored at the claim, and the
    only thing done to it is rehydration through `GradeDistribution.from_mapping`
    — spec §63 again, at the read boundary, so a document that does not sum to 1
    raises rather than being served. A refused entry is kept apart wearing its
    stored reason; an absent one is neither a refusal nor a prediction, and the
    `KeyError` is the corrupt record it is. `model_confidence` is what the engine
    takes as `distribution_confidence` — never the distribution's own spread.
    """
    predictions = document["predictions"]
    outlooks: list[CompanyOutlook] = []
    refusals: dict[str, InsufficientInformation] = {}
    for company in configuration.companies:
        entry = predictions[company]
        if "insufficient_information" in entry:
            refusals[company] = InsufficientInformation(entry["insufficient_information"])
            continue
        graded, raw = _graded_prices(observations, company, at=at, stale_after=stale_after)
        outlooks.append(
            company_outlook(
                company,
                GradeDistribution.from_mapping(entry["distribution"]),
                graded,
                raw,
                configuration.acquisition_cost,
                configuration.costs,
                distribution_confidence=Confidence(entry["model_confidence"]),
            )
        )
    return tuple(outlooks), refusals


def _image_quality(images: Iterable[ImageQuality]) -> Confidence | None:
    """The weakest photograph's verdict, or `None` when none was assessed.

    §44's one confidence is a minimum, never a product, and the same rule holds
    across the two sides. `None` rather than a fabricated zero: a photograph
    nobody assessed is not a bad one either (spec §2.7), and the caller answers
    `null` — nobody has asked — instead of an admission nothing measured.
    """
    scores = [image.quality_score for image in images if image.quality_score is not None]
    return None if not scores else Confidence(min(scores))


def _with_refusals(
    recommendation: Recommendation, refusals: Mapping[str, InsufficientInformation]
) -> Recommendation:
    """Put the companies whose model refused beside the engine's own unranked.

    §43's rule, one layer up: an undefined figure is unranked with its reason,
    never sorted last and never dropped. Nothing is recomputed — the order and
    the winner are the engine's. When nothing at all could be ranked the
    comparison is the engine's admission and stays so; `ResultsResponse.refused`
    is what carries the per-company reasons then (#238).
    """
    comparison = recommendation.comparison
    if not refusals or not isinstance(comparison, CompanyComparison):
        return recommendation
    return replace(
        recommendation,
        comparison=replace(comparison, unranked={**comparison.unranked, **refusals}),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _unreachable(reason: str, message: str) -> ApiError:
    """503 `provider_error`, naming which store would not answer.

    Three stores, three reasons, one PostgreSQL — an operator reading a log
    learns which statement failed rather than guessing from the path.
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        message,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": reason},
    )


async def _owned_analysis(db: AsyncSession, request: Request, analysis_id: UUID) -> AnalysisRecord:
    """The analysis, if this session started it. The same 404 for all four misses."""
    try:
        session_id = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
        record = None if session_id is None else await read_analysis(db, analysis_id, session_id)
    except AnalysisStoreUnavailable as error:
        logger.warning("economics.analysis_could_not_be_read", exc_info=True)
        raise _unreachable("analysis_store_unreachable", _UNREACHABLE) from error

    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return record


@router.post(
    "/{analysis_id}/economic-configuration",
    response_model=EconomicConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(analysis_rate_limit)],
    summary="Configure the economics of one analysis",
    description=(
        "Records spec §46's cost line items, §45's optional acquisition cost, the "
        "grading companies to compare and §43's optimization mode, and attaches "
        "them to the analysis as the immutable configuration spec §57's "
        "reproducibility record names.\n\n"
        "**Absent is not zero.** Omitting `acquisition_cost` means the user did not "
        "say, and the investment figures are then reported as `null` with "
        '`acquisition_cost_not_supplied`. `"0.00"` is a real acquisition cost. '
        "Nothing infers one.\n\n"
        "**A configuration is written once.** Spec §5 puts this step immediately "
        "after card confirmation, so an analysis takes one while it is `analyzing`; "
        "a second submission is a 409, and pricing the card differently is a new "
        "analysis rather than an edit."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No analysis is recorded under that identifier — for this caller. The "
                "bare 404 `GET /analyses/{id}` answers with."
            ),
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "The analysis is not ready for a configuration, or already has one. "
                "Outside the spec §66 taxonomy, which has no code meaning 'conflict'."
            ),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "The configuration is malformed — a negative amount, a selling-fee "
                "rate outside [0, 1], an unknown grading company or an unknown "
                "optimization mode. FastAPI's own validation body: spec §66 has no "
                "code for a malformed request, and forcing one would be a lie in the "
                "field callers trust."
            ),
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": (
                "Too many requests from this client (spec §55). Carries `Retry-After`. "
                "Outside the spec §66 taxonomy — see ADR 0005."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The analysis store or the configuration store could not be reached.",
        },
    },
)
async def configure_economics(
    request: Request,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    body: EconomicConfigurationRequest,
    analysis_id: Annotated[
        UUID,
        Path(description="The identifier `POST /analyses` answered with."),
    ],
) -> EconomicConfigurationResponse:
    """Store the configuration and attach it to the analysis.

    Ownership comes from `read_analysis`, so an unknown identifier, another
    session's analysis, a missing cookie and an expired one are the one 404 the
    other routes answer with.

    The conditional `UPDATE` inside `create_configuration` is the arbiter of a
    second submission, not the state read above: two requests arriving together
    both see `analyzing` and only one sets the column. The loser's row is dropped
    by the rollback, which is why the failure path rolls back rather than
    deleting anything.
    """
    record = await _owned_analysis(db, request, analysis_id)

    if record.status != _CONFIGURABLE:
        # Safe to name the state: ownership is established, and the caller can
        # already see it by polling.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The economics are configured once the card is confirmed, and this "
            f"analysis is {record.status}.",
        )

    try:
        stored = await create_configuration(
            db,
            record.id,
            costs=body.costs.to_domain(),
            acquisition_cost=None
            if body.acquisition_cost is None
            else Money(body.acquisition_cost),
            companies=body.grading_companies,
            optimization_mode=body.optimization_mode,
        )
        if stored is None:
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, _ALREADY_CONFIGURED)
        await db.commit()
    except EconomicConfigurationUnavailable as error:
        logger.warning("economics.configuration_not_stored", exc_info=True)
        raise _unreachable(
            "economic_configuration_store_unreachable", _CONFIGURATION_UNREACHABLE
        ) from error

    # Internal identifiers and the mode only. What a user paid for their card is
    # theirs, and a log is not the place for it.
    logger.info(
        "economics.configuration_recorded",
        analysis_id=str(record.id),
        economic_configuration_id=str(stored.id),
        optimization_mode=stored.optimization_mode,
        grading_companies=list(stored.companies),
        acquisition_cost_supplied=stored.acquisition_cost is not None,
    )
    return _configuration(stored)


async def _load_predictions(
    db: AsyncSession,
    record: AnalysisRecord,
    configuration: EconomicConfiguration,
    snapshot: MarketSnapshot | None,
    *,
    at: datetime,
    stale_after: timedelta,
) -> tuple[tuple[CompanyOutlook, ...], dict[str, InsufficientInformation]] | None:
    """Every configured company's figures, or `None` if the prediction step never ran.

    Reads what the worker stored (#227) and the prices the analysis's own
    snapshot holds for its confirmed card — never a provider (ADR 0006), never
    today's cut. An analysis with no snapshot still answers, with the engine's
    own refusals: M4's ingestion is blocked on a subscription and this route
    does not wait for it. The three reads raise three distinct 503 reasons, so
    an operator learns which statement failed rather than guessing from the path.
    """
    try:
        document = await read_grade_predictions(db, record.id)
    except AnalysisStoreUnavailable as error:
        logger.warning("economics.predictions_could_not_be_read", exc_info=True)
        raise _unreachable("analysis_store_unreachable", _UNREACHABLE) from error
    if document is None:
        return None

    observations: tuple[PriceObservation, ...] = ()
    if snapshot is not None and record.card_id is not None:
        try:
            card = await PostgresCardRepository(db).get(CardId(record.card_id))
        except CatalogUnavailable as error:
            logger.warning("economics.card_could_not_be_read", exc_info=True)
            raise _unreachable("catalog_unreachable", _CATALOG_UNREACHABLE) from error
        try:
            observations = () if card is None else await resolve_prices(db, snapshot, card)
        except MarketSnapshotUnavailable as error:
            logger.warning("economics.prices_could_not_be_read", exc_info=True)
            raise _unreachable("market_store_unreachable", _MARKET_UNREACHABLE) from error

    return _outlooks(document, configuration, observations, at=at, stale_after=stale_after)


@router.get(
    "/{analysis_id}/results",
    response_model=ResultsResponse,
    summary="The economics and recommendation for one analysis",
    description=(
        "Spec §64's results endpoint: §6's economics per company, §41's **two "
        "separately named** profit figures, ADR 0007's two ratios, the full grade "
        "distribution (§2.1) and §44's recommendation.\n\n"
        "**Nothing is conflated.** `incremental_grading_decision` answers 'should I "
        "grade the card I own?' and `investment_return` answers 'did buying it to "
        "grade make money?'. They share no field name, and neither ratio is called "
        "`roi`.\n\n"
        "**`companies` is empty and `recommendation` is `null` until the analysis "
        "has an economic configuration and the worker has stored its grade "
        "predictions.** That is an empty result rather than an error because the "
        "analysis is fine — it simply has not got there. Prices come from the "
        "snapshot the analysis recorded, never a provider; with no snapshot every "
        "figure is present-and-null beside the engine's own reason. A company whose "
        "model refused is in `refused` with its stored reason, keyed by slug — and in "
        "the comparison's `unranked` too, whenever another company could be "
        "ranked.\n\n"
        "`Cache-Control: no-store`: every figure here descends from prices whose "
        "confidence is discounted for age at the moment of asking."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No analysis is recorded under that identifier — for this caller. The "
                "bare 404 `GET /analyses/{id}` answers with."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": (
                "The analysis store, the configuration store or the market snapshot "
                "store could not be reached."
            ),
        },
    },
)
async def read_results(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    analysis_id: Annotated[
        UUID,
        Path(description="The identifier `POST /analyses` answered with."),
    ],
) -> ResultsResponse:
    """Lay out what this analysis has arrived at so far.

    Everything is read from the analysis's own §57 record — the configuration it
    references, the snapshot it was computed against and the predictions the
    worker stored — rather than resolved now. A result recomputed against
    whatever is current would answer a different question from the one the
    analysis asked. The one thing asked *now* is each price's age (#55), once,
    so every confidence in the body is mutually consistent.

    The recommendation needs three things and is `null` without any of them: a
    configuration (the mode and the thresholds), a stored prediction, and an
    assessed photograph (§44's third confidence source). `companies` needs the
    first two.
    """
    record = await _owned_analysis(db, request, analysis_id)

    try:
        configuration = (
            None
            if record.economic_configuration_id is None
            else await read_configuration(db, record.economic_configuration_id)
        )
    except EconomicConfigurationUnavailable as error:
        logger.warning("economics.configuration_could_not_be_read", exc_info=True)
        raise _unreachable(
            "economic_configuration_store_unreachable", _CONFIGURATION_UNREACHABLE
        ) from error

    try:
        snapshot = (
            None
            if record.market_snapshot_id is None
            else await get_snapshot(db, record.market_snapshot_id)
        )
    except MarketSnapshotUnavailable as error:
        logger.warning("economics.snapshot_could_not_be_read", exc_info=True)
        raise _unreachable("market_store_unreachable", _MARKET_UNREACHABLE) from error

    predicted = (
        None
        if configuration is None
        else await _load_predictions(
            db,
            record,
            configuration,
            snapshot,
            at=datetime.now(UTC),
            stale_after=timedelta(days=settings.market_stale_after_days),
        )
    )

    outlooks: tuple[CompanyOutlook, ...] = ()
    refusals: dict[str, InsufficientInformation] = {}
    recommendation: Recommendation | None = None
    if configuration is not None and predicted is not None:
        outlooks, refusals = predicted
        try:
            quality = _image_quality(await read_quality(db, record.id))
        except AnalysisStoreUnavailable as error:
            logger.warning("economics.image_quality_could_not_be_read", exc_info=True)
            raise _unreachable("analysis_store_unreachable", _UNREACHABLE) from error
        if quality is not None:
            recommendation = _with_refusals(
                recommend(
                    outlooks,
                    strategy_for(configuration.optimization_mode),
                    image_quality=quality,
                    thresholds=configuration.thresholds,
                ),
                refusals,
            )
        # Identifiers, which companies answered and the verdict — never a
        # probability or an amount (spec §54, and what a user paid is theirs).
        logger.info(
            "economics.results_computed",
            analysis_id=str(record.id),
            companies=[outlook.company for outlook in outlooks],
            refused={company: admission.reason for company, admission in refusals.items()},
            recommended_action=(
                None if recommendation is None else str(recommendation.recommended_action)
            ),
            market_snapshot_id=None if snapshot is None else str(snapshot.id),
        )

    refused = {company: admission.reason for company, admission in refusals.items()}
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return ResultsResponse(
        analysis_id=record.id,
        status=record.status,
        card_id=record.card_id,
        currency=(
            "SGD" if configuration is None else configuration.costs.grading_fee.currency.value
        ),
        economic_configuration=None if configuration is None else _configuration(configuration),
        market_snapshot=(
            None
            if snapshot is None
            else MarketSnapshotReference(
                id=snapshot.id,
                generated_at=snapshot.generated_at,
                data_version=str(snapshot.data_version),
            )
        ),
        companies=[_company_economics(outlook) for outlook in outlooks],
        refused=refused,
        recommendation=None if recommendation is None else _recommendation(recommendation),
    )
