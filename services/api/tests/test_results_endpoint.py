"""`GET /analyses/{id}/results` and the engine → wire mapping — #65.

Two halves, and the first is where the acceptance criterion lives.

**The mapping tests need no database.** The criterion is that "the incremental
grading decision and the investment return are separately named and never
conflated", which is a property of the response *shape*: they are asserted over
the models' own fields and over real engine results built here by hand. That is
also why they exist at all before anything predicts a grade — nothing yet
produces a `CompanyOutlook` at runtime (M8 does), so a request could not reach
these functions, and the contract would otherwise ship untested.

**The route tests need PostgreSQL**, on `test_analyses_endpoint.py`'s terms:
what the route has to get right is that one anonymous user cannot read another's
results.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.routers import economics
from tcg_api.routers.economics import (
    CompanyEconomicsResponse,
    IncrementalGradingDecisionResponse,
    InvestmentReturnResponse,
    RecommendationResponse,
    ResultsResponse,
)
from tcg_api.storage import get_object_storage
from tcg_domain.confidence import Confidence
from tcg_domain.distribution import GradeDistribution
from tcg_domain.grade import Grade, GradeBound
from tcg_domain.money import Money
from tcg_economic_engine import (
    CostConfiguration,
    GradedPrice,
    SellingFee,
    company_outlook,
    recommend,
    strategy_for,
)

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
    assert "recommendation" in ResultsResponse.model_fields
    assert "expected_profit" not in ResultsResponse.model_fields
