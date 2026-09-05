"""`GET /analyses/{id}/results` and the engine → wire mapping — #65.

Two halves, and the first is where the acceptance criterion lives.

**The mapping tests need no database.** The criterion is that "the incremental
grading decision and the investment return are separately named and never
conflated", which is a property of the response *shape*: they are asserted over
the models' own fields and over real engine results built here by hand. They
predate any runtime caller — the contract shipped at #65 and #228 filled it —
and so do the tests over the read side, which are pure over a stored document.

**The route tests need PostgreSQL**, on `test_analyses_endpoint.py`'s terms:
what the route has to get right is that one anonymous user cannot read another's
results, and since #228 that what the worker stored reaches the wire whole.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tcg_api.analysis.images import ImageQuality
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.economics.store import EconomicConfiguration
from tcg_api.market.snapshots import generate_snapshot
from tcg_api.routers import economics
from tcg_api.routers.economics import (
    CompanyEconomicsResponse,
    IncrementalGradingDecisionResponse,
    InvestmentReturnResponse,
    RecommendationResponse,
    ResultsResponse,
)
from tcg_api.storage import get_object_storage
from tcg_domain.card import CardReference
from tcg_domain.confidence import Confidence, InsufficientInformation
from tcg_domain.distribution import GradeDistribution
from tcg_domain.errors import InvalidGradeDistribution
from tcg_domain.grade import Grade, GradeBound
from tcg_domain.money import Money
from tcg_economic_engine import (
    DEFAULT_THRESHOLDS,
    CompanyComparison,
    CostConfiguration,
    GradedPrice,
    IncrementalGradingDecision,
    SellingFee,
    company_outlook,
    recommend,
    strategy_for,
)
from tcg_market_data import PriceObservation

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

TEN = Grade(Decimal("10"))
NINE = Grade(Decimal("9"))
EIGHT_OR_LOWER = Grade(Decimal("8"), GradeBound.OR_LOWER)

DISTRIBUTION = GradeDistribution({TEN: 0.2, NINE: 0.7, EIGHT_OR_LOWER: 0.1})

PRICES = {
    TEN: GradedPrice(Money.of("300.00"), Confidence(0.9)),
    NINE: GradedPrice(Money.of("120.00"), Confidence(0.8)),
    EIGHT_OR_LOWER: GradedPrice(Money.of("40.00"), Confidence(0.7)),
}

RAW = GradedPrice(Money.of("60.00"), Confidence(0.85))
PAID = Money.of("100.00")

COSTS = CostConfiguration(
    grading_fee=Money.of("40.00"),
    outbound_shipping=Money.of("15.00"),
    return_shipping=Money.of("15.00"),
    insurance=Money.of("5.00"),
    miscellaneous=Money.of("0.00"),
    selling_fee=SellingFee(rate=Decimal("0.10"), flat=Money.of("0.00")),
)


def outlook_for(
    company: str = "psa",
    *,
    raw: GradedPrice | None = RAW,
    acquisition_cost: Money | None = PAID,
    prices: dict[Grade, GradedPrice] | None = None,
) -> Any:
    return company_outlook(
        company,
        DISTRIBUTION,
        PRICES if prices is None else prices,
        raw,
        acquisition_cost,
        COSTS,
        distribution_confidence=Confidence(0.8),
    )


# ---------------------------------------------------------------------------
# The two §41 figures are never conflated — the acceptance criterion
# ---------------------------------------------------------------------------


def test_the_two_profit_figures_are_separately_named() -> None:
    """Spec §41: "This distinction is important and must be implemented rather than conflated.""" ""
    body = economics._company_economics(outlook_for())

    assert body.incremental_grading_decision is not None
    assert body.investment_return is not None
    assert body.incremental_grading_decision.incremental_profit
    assert body.investment_return.investment_profit


def test_the_two_profit_figures_share_no_field_name() -> None:
    """What makes it impossible to render one under the other's label.

    The engine asserts the same thing over `dataclasses.fields`; this asserts it
    over what a client actually receives, which is where the conflation would
    hurt.
    """
    incremental = set(IncrementalGradingDecisionResponse.model_fields)
    investment = set(InvestmentReturnResponse.model_fields)

    assert "incremental_profit" in incremental
    assert "investment_profit" in investment
    # `graded_proceeds`, `grading_costs` and the two uncertainty fields are the
    # same *quantity* on both sides — ADR 0007's shared numerator component —
    # and are the only names the two may have in common.
    assert incremental & investment == {
        "confidence",
        "graded_proceeds",
        "grading_costs",
        "unpriced_grades",
        "unpriced_probability",
    }
    assert not {name for name in incremental | investment if name == "expected_profit"}


def test_nothing_on_the_wire_is_called_roi_alone() -> None:
    """ADR 0007: two ratios, never one. A single headline number is a new ADR."""
    fields = set(CompanyEconomicsResponse.model_fields)

    assert "incremental_roi" in fields
    assert "investment_roi" in fields
    assert "roi" not in fields
    assert not {
        name
        for name in fields
        if name.endswith("_roi") and "incremental" not in name and "investment" not in name
    }


def test_nothing_on_the_wire_is_a_cost_total() -> None:
    """#58: named line items, never a total. §47's dimensions attach per line."""
    body = economics._company_economics(outlook_for())
    payload = body.model_dump()

    assert not [name for name in _every_key(payload) if "total" in name]


def _every_key(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from _every_key(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _every_key(item)


# ---------------------------------------------------------------------------
# What each figure carries
# ---------------------------------------------------------------------------


def test_the_full_distribution_is_returned() -> None:
    """Spec §2.1: retained in full, even when a UI shows one number."""
    body = economics._company_economics(outlook_for())

    assert [term.grade for term in body.grade_distribution] == ["8_or_lower", "9", "10"]
    assert sum(term.probability for term in body.grade_distribution) == pytest.approx(1.0)


def test_a_ratio_is_a_four_place_decimal_string() -> None:
    """ADR 0007 fixes four places; `Money`'s two are for money, not for a ratio."""
    body = economics._company_economics(outlook_for())

    assert body.incremental_roi is not None
    assert Decimal(body.incremental_roi.value).as_tuple().exponent == -4


def test_a_ratio_carries_the_label_the_adr_gave_it() -> None:
    """So nothing can display one ratio under the other's caption."""
    body = economics._company_economics(outlook_for())

    assert body.incremental_roi is not None
    assert body.investment_roi is not None
    assert body.incremental_roi.label != body.investment_roi.label


def test_an_absent_acquisition_cost_is_a_reason_and_never_a_zero() -> None:
    """ADR 0007's own string, reaching a client unchanged. §45 forbids inferring one."""
    body = economics._company_economics(outlook_for(acquisition_cost=None))

    assert body.investment_return is None
    assert body.investment_reason == "acquisition_cost_not_supplied"
    assert body.investment_roi is None
    assert body.investment_roi_reason == "acquisition_cost_not_supplied"
    # And the incremental figures are unaffected: they never see the number.
    assert body.incremental_grading_decision is not None
    assert body.incremental_roi is not None


def test_a_missing_raw_price_admits_with_its_own_reason() -> None:
    """#60's reason, deliberately not the expectation's — which side went missing matters."""
    body = economics._company_economics(outlook_for(raw=None))

    assert body.incremental_grading_decision is None
    assert body.incremental_reason == "no_raw_price_available"
    assert body.incremental_roi_reason == "no_raw_price_available"
    # The investment side still answers: it needs no raw price.
    assert body.investment_return is not None


def test_an_unpriced_ladder_admits_rather_than_valuing_a_grade_at_zero() -> None:
    """#59: an unpriced grade is excluded, never zero. Nothing priced is an admission."""
    body = economics._company_economics(outlook_for(prices={}))

    assert body.expected_graded_value is None
    assert body.expected_graded_value_reason == "no_graded_price_available"


def test_every_amount_is_a_decimal_string() -> None:
    """A JSON number is a float in most clients, and this is money."""
    body = economics._company_economics(outlook_for())

    assert body.incremental_grading_decision is not None
    assert isinstance(body.incremental_grading_decision.incremental_profit, str)
    assert Decimal(body.incremental_grading_decision.incremental_profit).as_tuple().exponent == -2


# ---------------------------------------------------------------------------
# The recommendation — spec §44
# ---------------------------------------------------------------------------


def recommendation_for(**kwargs: Any) -> RecommendationResponse:
    outlooks = [outlook_for("psa"), outlook_for("bgs")]
    answer = recommend(
        outlooks,
        strategy_for(kwargs.pop("mode", "expected_profit")),
        image_quality=kwargs.pop("image_quality", Confidence(0.9)),
    )
    return economics._recommendation(answer)


def test_the_recommendation_carries_an_action_a_reason_and_a_confidence() -> None:
    """§44's four outputs, and the reason is the evidence rather than a sentence."""
    body = recommendation_for()

    assert body.recommended_action in {"grade", "do_not_grade", "insufficient_information"}
    assert body.reason.code
    assert body.reason.figure
    assert 0.0 <= body.confidence <= 1.0


def test_the_reason_has_no_prose_field() -> None:
    """§50 forbids explanations unrelated to model evidence; #66 writes the copy."""
    fields = set(economics.ReasonResponse.model_fields)

    assert fields == {"code", "figure", "value", "threshold"}


def test_no_company_is_named_beside_an_admission() -> None:
    """§44's non-goal: a screen shown both renders the company as the answer."""
    body = recommendation_for(image_quality=Confidence(0.0))

    assert body.recommended_action == "insufficient_information"
    assert body.recommended_company is None
    # The comparison still travels: §49's compare table needs the order.
    assert body.comparison is not None


def test_the_comparison_reports_what_was_ranked_rather_than_a_mode_name() -> None:
    """#63: §43's `roi` is a mode, and `figure` names the number the order used."""
    body = recommendation_for(mode="roi")

    assert body.comparison is not None
    assert body.comparison.mode == "roi"
    assert {candidate.figure for candidate in body.comparison.ranked} == {"incremental_roi"}


def test_every_failed_gate_is_reported_and_not_only_the_decisive_one() -> None:
    """So a user who fixes the first is not sent into a second wall nobody mentioned."""
    body = recommendation_for(image_quality=Confidence(0.0))

    assert body.failed_gates
    assert body.reason.code == body.failed_gates[0].code


# ---------------------------------------------------------------------------
# Reading the stored predictions back — #228
# ---------------------------------------------------------------------------
# Pure over a stored document, a configuration and a snapshot's observations,
# so the rules — filtered on read, never re-predicted, a refusal kept apart,
# a missing price left missing — are asserted without a database.

AT = datetime(2026, 9, 4, tzinfo=UTC)
STALE_AFTER = timedelta(days=30)
NINE_POINT_FIVE = Grade(Decimal("9.5"))
CARD = CardReference(
    game="pokemon", language="en", set_code="BS", card_number="4/102", variant=None
)


def observation(
    amount: str,
    *,
    company: str | None = None,
    grade: Grade | None = None,
    age: timedelta = timedelta(0),
) -> PriceObservation:
    return PriceObservation(
        card=CARD,
        price=Money.of(amount),
        observed_at=AT - age,
        confidence=Confidence(0.9),
        provider="manual",
        grading_company=company,
        grade=grade,
    )


OBSERVATIONS = (
    observation("60.00"),
    observation("300.00", company="psa", grade=TEN),
    observation("120.00", company="psa", grade=NINE),
    observation("500.00", company="bgs", grade=NINE_POINT_FIVE),
)

PSA_ENTRY = {
    "distribution": DISTRIBUTION.as_mapping(),
    "model_confidence": 0.35,
    "model_version": "grading-psa-heuristic-v0.1.0",
}
REFUSED = {"insufficient_information": "condition_step_not_run"}


def document(**predictions: dict[str, Any]) -> dict[str, Any]:
    """What #227 stored: version and thresholds first, then one entry per company."""
    return {"version": "grading-v0", "thresholds": {}, "predictions": predictions}


def configuration_for(
    *companies: str,
    acquisition_cost: Money | None = PAID,
    mode: str = "expected_profit",
) -> EconomicConfiguration:
    return EconomicConfiguration(
        id=uuid.uuid4(),
        created_at=AT,
        costs=COSTS,
        acquisition_cost=acquisition_cost,
        companies=companies,
        optimization_mode=mode,
        thresholds=DEFAULT_THRESHOLDS,
    )


def test_graded_prices_key_one_companys_ladder_and_find_the_raw_price() -> None:
    """`GET /cards/{id}/market`'s keying, one company at a time; the raw price is shared."""
    graded, raw = economics._graded_prices(OBSERVATIONS, "psa", at=AT, stale_after=STALE_AFTER)

    assert set(graded) == {TEN, NINE}
    assert graded[TEN].value == Money.of("300.00")
    assert raw is not None
    assert raw.value == Money.of("60.00")


def test_another_companys_prices_do_not_leak_into_a_ladder() -> None:
    graded, _ = economics._graded_prices(OBSERVATIONS, "bgs", at=AT, stale_after=STALE_AFTER)

    assert set(graded) == {NINE_POINT_FIVE}


def test_a_prices_confidence_is_discounted_for_its_age() -> None:
    """#55: age is a question asked at the moment of the request, and it is asked here."""
    old = (observation("300.00", company="psa", grade=TEN, age=timedelta(days=60)),)

    graded, _ = economics._graded_prices(old, "psa", at=AT, stale_after=STALE_AFTER)

    assert graded[TEN].confidence.value < 0.9


def test_outlooks_follow_the_configuration_and_keep_a_refusal_apart() -> None:
    """Filtered to the configured companies on read, in their order; a refusal is not an outlook."""
    stored = document(bgs=REFUSED, psa=PSA_ENTRY, tag=PSA_ENTRY)

    outlooks, refusals = economics._outlooks(
        stored, configuration_for("bgs", "psa"), OBSERVATIONS, at=AT, stale_after=STALE_AFTER
    )

    assert [outlook.company for outlook in outlooks] == ["psa"]
    assert outlooks[0].distribution == DISTRIBUTION
    assert outlooks[0].distribution_confidence == Confidence(0.35)
    assert isinstance(outlooks[0].incremental, IncrementalGradingDecision)
    assert set(refusals) == {"bgs"}
    assert refusals["bgs"].reason == "condition_step_not_run"


def test_no_snapshot_answers_with_the_engines_own_reasons() -> None:
    """No price is an admission the engine already makes; nothing here fills one in."""
    outlooks, _ = economics._outlooks(
        document(psa=PSA_ENTRY), configuration_for("psa"), (), at=AT, stale_after=STALE_AFTER
    )

    body = economics._company_economics(outlooks[0])
    assert body.expected_graded_value_reason == "no_graded_price_available"
    assert body.incremental_reason == "no_raw_price_available"
    assert body.grade_distribution


def test_a_malformed_stored_distribution_is_refused_rather_than_served() -> None:
    """Spec §63 at the read boundary: a document that does not sum to 1 is a failure, not a result."""
    broken = {**PSA_ENTRY, "distribution": {"10": 0.7, "9": 0.7}}

    with pytest.raises(InvalidGradeDistribution):
        economics._outlooks(
            document(psa=broken), configuration_for("psa"), (), at=AT, stale_after=STALE_AFTER
        )


def test_image_quality_is_the_weakest_photograph() -> None:
    """§44's one confidence is a minimum, never a product — the same rule for the two sides."""
    images = [
        ImageQuality("back", 0.9, "good", None, "a" * 64),
        ImageQuality("front", 0.6, "poor", None, "b" * 64),
    ]

    assert economics._image_quality(images) == Confidence(0.6)


def test_image_quality_is_absent_when_no_photograph_was_assessed() -> None:
    """A photograph nobody assessed is not a good one (spec §2.7)."""
    assert economics._image_quality([ImageQuality("front", None, None, None, "a" * 64)]) is None
    assert economics._image_quality([]) is None


def test_a_refused_company_joins_the_unranked_beside_the_engines_admissions() -> None:
    """§43: unranked with its reason, never sorted last and never dropped."""
    answer = recommend(
        [outlook_for("psa")], strategy_for("expected_profit"), image_quality=Confidence(0.9)
    )

    merged = economics._with_refusals(answer, {"bgs": InsufficientInformation("model_declined")})

    assert isinstance(answer.comparison, CompanyComparison)
    assert isinstance(merged.comparison, CompanyComparison)
    assert merged.comparison.ranked == answer.comparison.ranked
    assert merged.comparison.unranked["bgs"].reason == "model_declined"
    assert merged.recommended_company == answer.recommended_company
    body = economics._recommendation(merged)
    assert body.comparison is not None
    assert [(entry.company, entry.reason) for entry in body.comparison.unranked] == [
        ("bgs", "model_declined")
    ]


def test_refusals_leave_an_admission_comparison_alone() -> None:
    """Nothing rankable is the engine's `no_company_can_be_ranked`, and stays so."""
    answer = recommend(
        [outlook_for("psa", raw=None, prices={})],
        strategy_for("expected_profit"),
        image_quality=Confidence(0.9),
    )

    merged = economics._with_refusals(answer, {"bgs": InsufficientInformation("model_declined")})

    assert merged is answer


def test_no_refusal_leaves_the_recommendation_untouched() -> None:
    answer = recommend(
        [outlook_for("psa")], strategy_for("expected_profit"), image_quality=Confidence(0.9)
    )

    assert economics._with_refusals(answer, {}) is answer


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    if not DATABASE_URL:
        return

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def executing(statement: str, **parameters: Any) -> None:
    async def write() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text(statement), parameters)
        finally:
            await engine.dispose()

    run(write)


@pytest.fixture
def client() -> Iterator[TestClient]:
    for cached in CACHES:
        cached.cache_clear()
    with TestClient(create_app()) as instance:
        yield instance


@pytest.mark.integration
@requires_postgres
def test_results_are_empty_rather_than_absent_before_anything_is_computed(
    client: TestClient,
) -> None:
    """No milestone predicts a grade yet, so this is today's answer for every analysis.

    Empty rather than an error, and `null` rather than
    `insufficient_information`: nothing has gone wrong and nothing has been
    asked, which is a third thing from "we asked and could not tell".
    """
    analysis_id = client.post("/analyses").json()["id"]

    response = client.get(f"/analyses/{analysis_id}/results")

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_id"] == analysis_id
    assert body["status"] == "created"
    assert body["companies"] == []
    assert body["refused"] == {}
    assert body["recommendation"] is None
    assert body["economic_configuration"] is None
    assert body["market_snapshot"] is None


@pytest.mark.integration
@requires_postgres
def test_the_results_echo_the_configuration_the_analysis_was_given(
    client: TestClient,
) -> None:
    """Spec §57 read back: the numbers this analysis was configured with, not today's."""
    analysis_id = client.post("/analyses").json()["id"]
    executing("UPDATE analyses SET status = 'analyzing' WHERE id = :id", id=uuid.UUID(analysis_id))
    stored = client.post(
        f"/analyses/{analysis_id}/economic-configuration",
        json={
            "acquisition_cost": "120.00",
            "grading_companies": ["psa"],
            "optimization_mode": "expected_profit",
        },
    ).json()

    body = client.get(f"/analyses/{analysis_id}/results").json()

    assert body["economic_configuration"] == stored
    assert body["currency"] == "SGD"


def analyzing(client: TestClient) -> str:
    """A fresh analysis, moved to where a configuration may be recorded."""
    analysis_id: str = client.post("/analyses").json()["id"]
    executing("UPDATE analyses SET status = 'analyzing' WHERE id = :id", id=uuid.UUID(analysis_id))
    return analysis_id


def configured(client: TestClient, analysis_id: str, *companies: str) -> None:
    response = client.post(
        f"/analyses/{analysis_id}/economic-configuration",
        json={
            "acquisition_cost": "100.00",
            "grading_companies": list(companies),
            "optimization_mode": "expected_profit",
        },
    )
    assert response.status_code == 201, response.text


def predicted(analysis_id: str, **entries: dict[str, Any]) -> None:
    """What #227's worker stored, written straight: the document is the contract."""
    executing(
        "UPDATE analyses SET grade_predictions = CAST(:document AS jsonb) WHERE id = :id",
        document=json.dumps(document(**entries)),
        id=uuid.UUID(analysis_id),
    )


def photographed(analysis_id: str, *, score: float) -> None:
    """One assessed image, so §44's third confidence source exists."""
    executing(
        "INSERT INTO images (id, analysis_id, side, original_uri, mime_type, sha256, "
        "quality_score, quality_status) VALUES "
        "(:image_id, :id, 'front', 'uploads/front', 'image/jpeg', :digest, :score, 'good')",
        image_id=uuid.uuid4(),
        id=uuid.UUID(analysis_id),
        digest=uuid.uuid4().hex * 2,
        score=score,
    )


BGS_ENTRY = {
    "distribution": {"9": 0.5, "9.5": 0.4, "10": 0.1},
    "model_confidence": 0.35,
    "model_version": "grading-bgs-heuristic-v0.1.0",
}
STORED_PSA_ENTRY = {**PSA_ENTRY, "distribution": {"8": 0.1, "9": 0.7, "10": 0.2}}


@pytest.mark.integration
@requires_postgres
def test_two_predicted_companies_answer_with_their_distributions_and_a_recommendation(
    client: TestClient,
) -> None:
    """M8's acceptance criterion on the wire, on a deployment that has never ingested.

    No snapshot is today's state (ADR 0006), so every priced figure is the
    engine's own admission — and the recommendation is asked and declines,
    which is a different thing from `null`.
    """
    analysis_id = analyzing(client)
    configured(client, analysis_id, "bgs", "psa")
    predicted(analysis_id, bgs=BGS_ENTRY, psa=STORED_PSA_ENTRY, tag=REFUSED)
    photographed(analysis_id, score=0.9)

    body = client.get(f"/analyses/{analysis_id}/results").json()

    assert [company["company"] for company in body["companies"]] == ["bgs", "psa"]
    for company in body["companies"]:
        distribution = {
            term["grade"]: term["probability"] for term in company["grade_distribution"]
        }
        assert sum(distribution.values()) == pytest.approx(1.0)
        assert company["distribution_confidence"] == pytest.approx(0.35)
        assert company["expected_graded_value"] is None
        assert company["expected_graded_value_reason"] == "no_graded_price_available"
        assert company["incremental_reason"] == "no_raw_price_available"
    bgs, psa = body["companies"]
    assert "9.5" in {term["grade"] for term in bgs["grade_distribution"]}
    assert "9.5" not in {term["grade"] for term in psa["grade_distribution"]}
    recommendation = body["recommendation"]
    assert recommendation is not None
    assert recommendation["recommended_action"] == "insufficient_information"
    assert recommendation["recommended_company"] is None
    assert recommendation["comparison_reason"] == "no_company_can_be_ranked"
    assert recommendation["image_quality"] == pytest.approx(0.9)


@pytest.mark.integration
@requires_postgres
def test_a_refused_company_is_not_a_companies_entry(client: TestClient) -> None:
    """A refusal has no distribution to carry; it is unranked, and never fabricated."""
    analysis_id = analyzing(client)
    configured(client, analysis_id, "psa", "bgs")
    predicted(analysis_id, psa=STORED_PSA_ENTRY, bgs=REFUSED, tag=REFUSED)
    photographed(analysis_id, score=0.9)

    body = client.get(f"/analyses/{analysis_id}/results").json()

    assert [company["company"] for company in body["companies"]] == ["psa"]
    assert body["refused"] == {"bgs": "condition_step_not_run"}
    assert body["recommendation"] is not None


@pytest.mark.integration
@requires_postgres
def test_every_configured_company_refused_still_answers_each_reason(
    client: TestClient,
) -> None:
    """#238: with nothing to rank there is no comparison to carry a refusal.

    The engine's `no_company_can_be_ranked` is still its answer, and `refused`
    on the body is where each company's stored reason travels instead.
    """
    analysis_id = analyzing(client)
    configured(client, analysis_id, "psa", "bgs")
    predicted(analysis_id, psa=REFUSED, bgs=REFUSED, tag=REFUSED)
    photographed(analysis_id, score=0.9)

    response = client.get(f"/analyses/{analysis_id}/results")

    assert response.status_code == 200
    body = response.json()
    assert body["companies"] == []
    assert body["refused"] == {
        "psa": "condition_step_not_run",
        "bgs": "condition_step_not_run",
    }
    recommendation = body["recommendation"]
    assert recommendation["recommended_action"] == "insufficient_information"
    assert recommendation["recommended_company"] is None
    assert recommendation["comparison"] is None
    assert recommendation["comparison_reason"] == "no_company_can_be_ranked"


@pytest.mark.integration
@requires_postgres
def test_predictions_without_a_configuration_answer_as_before(client: TestClient) -> None:
    """Nobody has said which companies to compare, so nobody has asked."""
    analysis_id = analyzing(client)
    predicted(analysis_id, psa=STORED_PSA_ENTRY, bgs=BGS_ENTRY, tag=REFUSED)
    photographed(analysis_id, score=0.9)

    body = client.get(f"/analyses/{analysis_id}/results").json()

    assert body["companies"] == []
    assert body["recommendation"] is None


@pytest.mark.integration
@requires_postgres
def test_a_configuration_without_predictions_answers_as_before(client: TestClient) -> None:
    """NULL means the step never ran — still `[]` and `null`, not an admission."""
    analysis_id = analyzing(client)
    configured(client, analysis_id, "psa")

    body = client.get(f"/analyses/{analysis_id}/results").json()

    assert body["status"] == "completed"
    assert body["companies"] == []
    assert body["refused"] == {}
    assert body["recommendation"] is None


@pytest.mark.integration
@requires_postgres
def test_results_answer_the_same_on_completed_and_on_analyzing(client: TestClient) -> None:
    """#244 drew the line: a configuration completes the analysis. The route
    composes from stored pieces and does not care which side of it it reads
    from, so the same inputs answer the same body either way."""
    bodies: list[dict[str, Any]] = []
    for rewound in (False, True):
        analysis_id = analyzing(client)
        configured(client, analysis_id, "bgs", "psa")
        predicted(analysis_id, bgs=BGS_ENTRY, psa=STORED_PSA_ENTRY, tag=REFUSED)
        photographed(analysis_id, score=0.9)
        if rewound:
            # `status` and `completed_at` are outside the immutability trigger.
            executing(
                "UPDATE analyses SET status = 'analyzing', completed_at = NULL WHERE id = :id",
                id=uuid.UUID(analysis_id),
            )
        bodies.append(client.get(f"/analyses/{analysis_id}/results").json())

    statuses = [body.pop("status") for body in bodies]
    assert statuses == ["completed", "analyzing"]
    for body in bodies:
        body.pop("analysis_id")
        body["economic_configuration"].pop("id")
        body["economic_configuration"].pop("created_at")
    assert bodies[0] == bodies[1]
    assert bodies[0]["recommendation"] is not None


@pytest.mark.integration
@requires_postgres
def test_no_assessed_photograph_means_no_recommendation(client: TestClient) -> None:
    """The figures still travel; §44's answer needs the third confidence source."""
    analysis_id = analyzing(client)
    configured(client, analysis_id, "psa")
    predicted(analysis_id, psa=STORED_PSA_ENTRY, bgs=REFUSED, tag=REFUSED)

    body = client.get(f"/analyses/{analysis_id}/results").json()

    assert [company["company"] for company in body["companies"]] == ["psa"]
    assert body["recommendation"] is None


SET_ID = uuid.UUID("22222222-2222-5222-8222-222222222228")
CARD_ID = uuid.UUID("33333333-3333-5333-8333-333333333338")
PROVIDER_ID = uuid.UUID("66666666-6666-5666-8666-666666666668")


def priced_card() -> uuid.UUID:
    """A card with a raw price and a PSA ladder in a snapshot cut after them."""
    executing(
        "INSERT INTO sets (id, game, language, set_code, name) "
        "VALUES (:id, 'pokemon', 'en', 'RES', 'Results Test Set') ON CONFLICT (id) DO NOTHING",
        id=SET_ID,
    )
    executing(
        "INSERT INTO cards (id, game, language, set_id, card_number, name) "
        "VALUES (:id, 'pokemon', 'en', :set_id, '4/102', 'Results Test Card') "
        "ON CONFLICT (id) DO NOTHING",
        id=CARD_ID,
        set_id=SET_ID,
    )
    executing(
        "INSERT INTO market_providers "
        "(id, slug, name, license, commercial_use, terms_reference, verified_on) "
        "VALUES (:id, 'resultsource', 'ResultSource', 'Commercial use permitted.', true, "
        "'https://example.test/terms', current_date) ON CONFLICT (id) DO NOTHING",
        id=PROVIDER_ID,
    )
    for company, grade, price in (
        (None, None, "60.00"),
        ("psa", "10", "300.00"),
        ("psa", "9", "120.00"),
        ("psa", "8", "40.00"),
    ):
        executing(
            "INSERT INTO market_observations "
            "(id, card_id, provider_id, currency, price, confidence, observed_at, "
            "grading_company, grade) VALUES "
            "(:id, :card, :provider, 'SGD', :price, 0.9, now(), :company, :grade)",
            id=uuid.uuid4(),
            card=CARD_ID,
            provider=PROVIDER_ID,
            price=Decimal(price),
            company=company,
            grade=grade,
        )

    async def cut() -> uuid.UUID:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with AsyncSession(engine) as session:
                snapshot = await generate_snapshot(session, provider_id=PROVIDER_ID)
                await session.commit()
                return snapshot.id
        finally:
            await engine.dispose()

    return run(cut)


@pytest.fixture
def priced() -> Iterator[list[dict[str, Any]]]:
    """Collects the priced results, and takes the snapshot away again afterwards.

    A snapshot left in the shared database is one the worker would record on
    the next run — `test_analyses_endpoint.py` asserts there is none — so
    what this module cuts, it removes, in FK order.
    """
    bodies: list[dict[str, Any]] = []
    yield bodies
    for body in bodies:
        executing("DELETE FROM analyses WHERE id = :id", id=uuid.UUID(body["analysis_id"]))
        executing(
            "DELETE FROM economic_configurations WHERE id = :id",
            id=uuid.UUID(body["economic_configuration"]["id"]),
        )
    executing("DELETE FROM market_snapshots WHERE provider_id = :id", id=PROVIDER_ID)
    executing("DELETE FROM market_observations WHERE provider_id = :id", id=PROVIDER_ID)
    executing("DELETE FROM market_providers WHERE id = :id", id=PROVIDER_ID)


def priced_analysis(
    client: TestClient, psa: dict[str, Any], bodies: list[dict[str, Any]]
) -> dict[str, Any]:
    """An analysis of the priced card, configured for PSA and BGS, with `psa` stored."""
    snapshot_id = priced_card()
    analysis_id = analyzing(client)
    executing(
        "UPDATE analyses SET card_id = :card, market_snapshot_id = :snapshot WHERE id = :id",
        card=CARD_ID,
        snapshot=snapshot_id,
        id=uuid.UUID(analysis_id),
    )
    configured(client, analysis_id, "psa", "bgs")
    predicted(analysis_id, psa=psa, bgs=BGS_ENTRY, tag=REFUSED)
    photographed(analysis_id, score=0.9)

    body: dict[str, Any] = client.get(f"/analyses/{analysis_id}/results").json()
    bodies.append(body)
    assert body["market_snapshot"]["id"] == str(snapshot_id)
    return body


@pytest.mark.integration
@requires_postgres
def test_a_priced_card_is_valued_from_its_own_snapshot(
    client: TestClient, priced: list[dict[str, Any]]
) -> None:
    """The whole M5 chain on real rows: prices from the analysis's recorded cut, never today's."""
    body = priced_analysis(client, STORED_PSA_ENTRY, priced)

    psa, bgs = body["companies"]
    assert psa["expected_graded_value"] is not None
    assert psa["expected_graded_value"]["unpriced_grades"] == []
    assert psa["incremental_grading_decision"] is not None
    assert psa["incremental_grading_decision"]["raw_market_value"] == "60.00"
    assert psa["investment_return"]["acquisition_cost"] == "100.00"
    # BGS has no ladder in this snapshot: an admission per figure, never a zero.
    assert bgs["expected_graded_value"] is None
    assert bgs["expected_graded_value_reason"] == "no_graded_price_available"
    comparison = body["recommendation"]["comparison"]
    assert comparison["ranked"][0]["company"] == "psa"
    assert [entry["company"] for entry in comparison["unranked"]] == ["bgs"]
    # BGS is the engine's own unranked, not a refusal: `refused` says nothing of it.
    assert body["refused"] == {}


@pytest.mark.integration
@requires_postgres
def test_the_heuristic_predictors_declared_confidence_is_below_the_gate(
    client: TestClient, priced: list[dict[str, Any]]
) -> None:
    """What V1 actually answers, and why: #223 to #225 cap `model_confidence` at 0.35.

    ADR 0011's declared-uncertainty baseline sits below #64's provisional
    `minimum_grade_confidence`, so the engine is asked and declines — an
    admission with the gate, its value and its threshold, which is the honest
    answer until a calibrated model raises the first or calibration moves the
    second. The comparison still travels for §49's table.
    """
    body = priced_analysis(client, STORED_PSA_ENTRY, priced)

    recommendation = body["recommendation"]
    assert recommendation["recommended_action"] == "insufficient_information"
    assert recommendation["recommended_company"] is None
    assert recommendation["reason"]["code"] == "grade_confidence_below_threshold"
    assert recommendation["reason"]["figure"] == "distribution_confidence"
    assert recommendation["reason"]["value"] == "0.35"
    assert Decimal(recommendation["reason"]["threshold"]) > Decimal("0.35")
    assert recommendation["comparison"] is not None


@pytest.mark.integration
@requires_postgres
def test_a_confident_prediction_reaches_a_verdict(
    client: TestClient, priced: list[dict[str, Any]]
) -> None:
    """The chain a calibrated model completes: every gate clears and the economics decide."""
    body = priced_analysis(client, {**STORED_PSA_ENTRY, "model_confidence": 0.8}, priced)

    recommendation = body["recommendation"]
    assert recommendation["recommended_action"] in {"grade", "do_not_grade"}
    assert recommendation["recommended_company"] == "psa"
    assert recommendation["reason"]["figure"] == "incremental_profit"
    assert recommendation["failed_gates"] == []
    assert recommendation["grade_confidence"] == pytest.approx(0.8)


@pytest.mark.integration
@requires_postgres
def test_the_results_are_never_cached(client: TestClient) -> None:
    """Every figure descends from prices whose age is computed at the moment of asking."""
    analysis_id = client.post("/analyses").json()["id"]

    response = client.get(f"/analyses/{analysis_id}/results")

    assert response.headers["cache-control"] == "no-store"


@pytest.mark.integration
@requires_postgres
def test_another_sessions_results_are_the_same_404_as_an_unknown_analysis(
    client: TestClient,
) -> None:
    analysis_id = client.post("/analyses").json()["id"]
    client.cookies.clear()

    unknown = client.get(f"/analyses/{uuid.uuid4()}/results")
    someone_elses = client.get(f"/analyses/{analysis_id}/results")

    assert unknown.status_code == someone_elses.status_code == 404
    assert unknown.json() == someone_elses.json()


@pytest.mark.integration
@requires_postgres
def test_a_malformed_analysis_id_is_a_422(client: TestClient) -> None:
    assert client.get("/analyses/not-a-uuid/results").status_code == 422


def test_the_results_model_keeps_the_two_figures_apart_at_the_top_level() -> None:
    """A client parsing the envelope alone can already tell them apart."""
    assert "companies" in ResultsResponse.model_fields
    assert "refused" in ResultsResponse.model_fields
    assert "recommendation" in ResultsResponse.model_fields
    assert "expected_profit" not in ResultsResponse.model_fields
