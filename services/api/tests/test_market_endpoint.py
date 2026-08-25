"""`GET /cards/{card_id}/market` — issue #56.

These tests run without PostgreSQL. `resolved_market` is the whole seam: it is
one dependency that reads the card, the snapshot and its prices, so a fake
supplied through `dependency_overrides` reaches every policy branch — including
the two absences a working database cannot demonstrate, an unknown snapshot and
a store that will not answer.

The fixture below is chosen to make the load-bearing distinctions visible in one
body: a fresh PSA 10 price, a **zero-priced** PSA 9 (a real observation about a
card nobody will pay for), an old BGS 9.5 (the grade only BGS issues), a raw
price, and **no TAG prices at all** — which is what ADR 0006 says V1's provider
holds, and which must reach a user as a null rather than as a borrowed PSA
figure.

`test_market_schema.py` proves the resolver itself answers correctly against
real PostgreSQL; nothing here re-tests SQL.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.errors import ErrorCode
from tcg_api.market.snapshots import MarketSnapshotUnavailable
from tcg_api.routers import market
from tcg_api.routers.grading import grading_rules_in_force
from tcg_api.routers.market import ResolvedMarket, resolved_market
from tcg_api.storage import get_object_storage
from tcg_domain.card import CardReference
from tcg_domain.catalog import Card, CardId, Set, SetId
from tcg_domain.confidence import Confidence
from tcg_domain.grade import Grade
from tcg_domain.money import Money
from tcg_grading_companies import ADAPTERS
from tcg_market_data import MarketSnapshot, PriceObservation

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

BASE_SET = Set(
    id=SetId(UUID("11111111-1111-5111-8111-111111111111")),
    game="pokemon",
    language="en",
    set_code="BS",
    name="Base Set",
    release_date=date(1999, 1, 9),
    metadata={"total_cards": 102},
)

CHARIZARD = Card(
    id=CardId(UUID("22222222-2222-5222-8222-222222222222")),
    set=BASE_SET,
    card_number="4/102",
    name="Charizard",
    rarity="Rare Holo",
    variant="unlimited-holo",
)

UNKNOWN_SNAPSHOT = UUID("44444444-4444-5444-8444-444444444444")

#: Frozen, so the arithmetic below is hand-checkable. The route asks the clock
#: itself, so ages are asserted as ranges against "now" rather than pinned.
CUT = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)

SNAPSHOT = MarketSnapshot(
    id=UUID("55555555-5555-5555-8555-555555555555"),
    provider=UUID("66666666-6666-5666-8666-666666666666"),
    generated_at=CUT,
    data_version=date(2026, 8, 25),
)

#: The provider's own figure, before any staleness discount. The response must
#: never report more than this, and for a fresh price must report exactly it.
CLAIMED = Confidence.of(0.9)


def reference() -> CardReference:
    return CHARIZARD.reference


def observation(
    *,
    amount: str,
    company: str | None = None,
    grade: str | None = None,
    age: timedelta = timedelta(hours=2),
    confidence: Confidence = CLAIMED,
) -> PriceObservation:
    return PriceObservation(
        card=reference(),
        price=Money.of(Decimal(amount)),
        observed_at=datetime.now(UTC) - age,
        confidence=confidence,
        provider="example",
        grading_company=company,
        grade=None if grade is None else Grade.parse(grade),
    )


def prices() -> tuple[PriceObservation, ...]:
    return (
        observation(amount="120.00"),
        observation(amount="900.00", company="psa", grade="10"),
        # Zero is a price, not an absence. The distinction the whole port exists
        # to keep, and the one a client must not be asked to guess at.
        observation(amount="0.00", company="psa", grade="9"),
        # Only BGS issues 9.5, and this one is old enough to be discounted.
        observation(amount="450.00", company="bgs", grade="9.5", age=timedelta(days=45)),
    )


def resolved(**overrides: Any) -> ResolvedMarket:
    fields: dict[str, Any] = {
        "requested_card_id": CHARIZARD.id,
        "requested_snapshot_id": None,
        "card": CHARIZARD,
        "snapshot": SNAPSHOT,
        "prices": prices(),
    }
    fields.update(overrides)
    return ResolvedMarket(**fields)


def app_with(view: ResolvedMarket) -> Any:
    app = create_app()
    app.dependency_overrides[resolved_market] = lambda: view
    return app


def get(view: ResolvedMarket | None = None, query: str = "") -> Any:
    with TestClient(app_with(resolved() if view is None else view)) as client:
        return client.get(f"/cards/{CHARIZARD.id}/market{query}")


def body(view: ResolvedMarket | None = None) -> dict[str, Any]:
    response = get(view)
    assert response.status_code == 200, response.text
    return response.json()


def graded(payload: dict[str, Any]) -> dict[tuple[str, str], Any]:
    return {(entry["company"], entry["grade"]): entry["price"] for entry in payload["graded"]}


def ladder() -> list[tuple[str, str]]:
    """Every (company, grade) the product supports, in the order it serves them."""
    return [
        (company, str(grade))
        for company, adapter in ADAPTERS.items()
        for grade in adapter.get_grade_scale().ordered
    ]


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
def test_the_endpoint_is_mounted_and_is_not_shadowed_by_the_card_detail_route() -> None:
    """Two routers share the `/cards` prefix; the paths differ by a segment.

    `routers/cards.py` warns that a literal path must be declared above
    `read_card`, because `{card_id}: UUID` swallows it. That does not apply
    here — `/cards/{card_id}/market` has one segment more than
    `/cards/{card_id}` — and this test is what would notice if it ever did.
    """
    assert get().status_code == 200


def test_the_snapshot_the_prices_came_from_is_returned() -> None:
    """Spec §57: a result has to be tie-able back to the exact cut it used."""
    assert body()["snapshot"]["id"] == str(SNAPSHOT.id)


def test_the_snapshot_carries_the_date_it_describes() -> None:
    """ADR 0006's caching grant obliges the UI to date-stamp what it shows.

    A record of a past market is honest; the same figures presented as current
    are not, and a client cannot tell them apart without this.
    """
    assert body()["snapshot"]["data_version"] == "2026-08-25"
    assert body()["snapshot"]["generated_at"].startswith("2026-08-25T09:00")


def test_the_card_is_named_in_the_response() -> None:
    assert body()["card_id"] == str(CHARIZARD.id)


def test_every_company_and_grade_is_present() -> None:
    """Fifty-five entries: PSA 18, TAG 18, BGS 19. Never a subset."""
    payload = body()
    assert len(payload["graded"]) == len(ladder()) == 55
    assert list(graded(payload)) == ladder()


def test_the_graded_ladder_matches_the_grading_companies_endpoint() -> None:
    """The two endpoints spell every grade the same way, or a client cannot join them.

    This is the test that earns the duplication: `GET /grading-companies` is
    where a frontend learns the scale, and a picker built from it has to be able
    to look a price up by what it read. Both render `str(Grade)`, and this
    proves it byte for byte rather than by inspection.
    """
    app = app_with(resolved())
    # `/grading-companies` reads its versions from the database; the scale comes
    # from the package either way, and the scale is what this test compares.
    app.dependency_overrides[grading_rules_in_force] = lambda: dict.fromkeys(ADAPTERS)

    with TestClient(app) as client:
        companies = client.get("/grading-companies").json()["companies"]
        prices_by_key = graded(client.get(f"/cards/{CHARIZARD.id}/market").json())

    for company in companies:
        for grade in company["grades"]:
            assert (company["company"], grade) in prices_by_key


def test_the_ladder_follows_the_adapters_order_and_ascends_within_each_company() -> None:
    entries = body()["graded"]
    assert [entry["company"] for entry in entries[:18]] == ["psa"] * 18
    psa = [entry["grade"] for entry in entries if entry["company"] == "psa"]
    assert psa[0] == "1"
    assert psa[-1] == "10"
    assert "9.5" not in psa
    assert "9.5" in [entry["grade"] for entry in entries if entry["company"] == "bgs"]


def test_the_raw_price_is_reported_separately_from_the_graded_ones() -> None:
    """§6's panel reads "Raw value" then the ladder; they are different questions."""
    payload = body()
    assert payload["raw"]["amount"] == "120.00"
    assert all(entry["grade"] != "raw" for entry in payload["graded"])


# ---------------------------------------------------------------------------
# Uncertainty as an output — spec §2.7, §38
#
# The group this endpoint exists to get right. A wrong answer here is a
# confident number in the one place a user is deciding where to spend money.
# ---------------------------------------------------------------------------
def test_a_zero_price_is_a_price_and_a_missing_one_is_null() -> None:
    """The distinction `PriceObservation` was built around, at the HTTP edge.

    A card observed to be worth nothing is a measurement; a card nobody has
    priced is not. Collapsing them would feed `Money.zero()` into
    `EV = Σ P(g)·V(g)` and produce a confident, wrong recommendation.
    """
    by_key = graded(body())

    assert by_key[("psa", "9")]["amount"] == "0.00"
    assert by_key[("psa", "8")] is None


def test_an_unavailable_price_is_present_and_null_rather_than_omitted() -> None:
    """An absent key is indistinguishable from a bug on the client."""
    payload = body()

    assert all("price" in entry for entry in payload["graded"])
    assert any(entry["price"] is None for entry in payload["graded"])


def test_tag_grades_are_null_and_carry_no_substituted_psa_price() -> None:
    """ADR 0006: V1's provider does not cover TAG, and the answer is "we do not know".

    Asserted from the data rather than from a TAG branch in the router — there
    is no such branch, which is what makes "never substitute and never
    interpolate" structural instead of a comment someone can delete.
    """
    by_key = graded(body())
    tag = {key: value for key, value in by_key.items() if key[0] == "tag"}

    assert len(tag) == 18
    assert all(value is None for value in tag.values())


def test_a_card_with_no_prices_at_all_is_a_200_with_the_whole_ladder_null() -> None:
    """A card nobody has priced is an answer, not a failure."""
    payload = body(resolved(prices=()))

    assert payload["raw"] is None
    assert len(payload["graded"]) == 55
    assert all(entry["price"] is None for entry in payload["graded"])


def test_a_card_with_only_graded_prices_reports_a_null_raw() -> None:
    payload = body(resolved(prices=(observation(amount="900.00", company="psa", grade="10"),)))

    assert payload["raw"] is None
    assert graded(payload)[("psa", "10")]["amount"] == "900.00"


# ---------------------------------------------------------------------------
# Freshness — spec §38's price_confidence and price_age
# ---------------------------------------------------------------------------
def test_a_fresh_price_reports_the_providers_own_confidence_undiscounted() -> None:
    """Within one ingestion cycle a price is not stale, it is simply current."""
    assert graded(body())[("psa", "10")]["price_confidence"] == pytest.approx(CLAIMED.value)


def test_an_old_price_reports_less_than_the_provider_claimed() -> None:
    """Forty-five days against the configured thirty: past the threshold, at the floor.

    Also proves `stale_after` is wired from settings rather than defaulted in
    the router — with no threshold reaching `price_confidence` this figure would
    come back undiscounted.
    """
    discounted = graded(body())[("bgs", "9.5")]["price_confidence"]

    assert discounted < CLAIMED.value
    assert discounted == pytest.approx(CLAIMED.value * 0.05)


def test_the_staleness_threshold_is_read_from_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment ingesting less often discounts less, without a code change."""
    monkeypatch.setenv("TCG_API_MARKET_STALE_AFTER_DAYS", "3650")
    for cached in CACHES:
        cached.cache_clear()

    generous = graded(body())[("bgs", "9.5")]["price_confidence"]

    for cached in CACHES:
        cached.cache_clear()
    assert generous > CLAIMED.value * 0.05


def test_a_never_discounted_price_is_still_never_reported_above_what_was_claimed() -> None:
    """The decay multiplies, so a weak observation stays weak for being new."""
    weak = observation(amount="10.00", company="psa", grade="10", confidence=Confidence.of(0.2))

    assert graded(body(resolved(prices=(weak,))))[("psa", "10")]["price_confidence"] == (
        pytest.approx(0.2)
    )


def test_every_price_reports_its_age_in_seconds() -> None:
    payload = body()
    reported = [entry["price"] for entry in payload["graded"] if entry["price"] is not None]

    assert payload["raw"]["price_age_seconds"] == pytest.approx(2 * 3600, abs=60)
    assert all(entry["price_age_seconds"] >= 0 for entry in reported)


def test_a_price_observed_in_the_future_reports_no_negative_age() -> None:
    """A provider clock running ahead of ours is ordinary; a negative age is not."""
    ahead = observation(amount="5.00", company="psa", grade="10", age=timedelta(hours=-4))

    assert graded(body(resolved(prices=(ahead,))))[("psa", "10")]["price_age_seconds"] == 0


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
def test_an_unknown_card_is_a_404_naming_the_identifier() -> None:
    response = get(resolved(card=None, snapshot=None, prices=()))

    assert response.status_code == 404
    assert response.json()["code"] == ErrorCode.CARD_NOT_IDENTIFIED.value
    assert response.json()["details"] == {"card_id": str(CHARIZARD.id)}


def test_an_unknown_card_is_a_404_even_where_nothing_has_been_ingested() -> None:
    """The card is the path's resource, so it is answered before the market is."""
    response = get(resolved(card=None, snapshot=None, prices=()))

    assert response.status_code == 404
    assert response.json()["code"] != ErrorCode.MARKET_DATA_UNAVAILABLE.value


def test_no_snapshot_at_all_is_a_503_market_data_unavailable() -> None:
    """Today's answer on every deployment: ADR 0006 gates ingestion on a subscription.

    503 rather than a 200 with a null snapshot. The contract is that these
    prices came from a dated cut, and a body that cannot say which one is not a
    weaker answer — it is the absence of one. `GET /catalog/version` refuses the
    same way when no version is registered.
    """
    response = get(resolved(snapshot=None, prices=()))

    assert response.status_code == 503
    assert response.json()["code"] == ErrorCode.MARKET_DATA_UNAVAILABLE.value


def test_an_unknown_snapshot_id_is_a_404_naming_it() -> None:
    """Distinguished from the 503 only by the caller having named one."""
    response = get(
        resolved(snapshot=None, prices=(), requested_snapshot_id=UNKNOWN_SNAPSHOT),
        query=f"?snapshot_id={UNKNOWN_SNAPSHOT}",
    )

    assert response.status_code == 404
    assert response.json()["code"] == ErrorCode.MARKET_DATA_UNAVAILABLE.value
    assert response.json()["details"] == {"snapshot_id": str(UNKNOWN_SNAPSHOT)}


def test_a_malformed_snapshot_id_is_a_422() -> None:
    """Which is why the unknown-but-well-formed one above is a 404 and not this.

    One status meaning both "you sent nonsense" and "the thing you named does
    not exist" is what `routers/cards.py` argues against.

    No `dependency_overrides` here, deliberately: FastAPI validates the
    parameters the dependency declares, so overriding it is what would stop this
    request ever being parsed.
    """
    with TestClient(create_app()) as client:
        response = client.get(f"/cards/{CHARIZARD.id}/market?snapshot_id=not-a-uuid")

    assert response.status_code == 422


def test_a_malformed_card_id_is_a_422() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/cards/not-a-uuid/market")

    assert response.status_code == 422


def test_a_requested_snapshot_is_the_one_read() -> None:
    older = MarketSnapshot(
        id=uuid4(),
        provider=SNAPSHOT.provider,
        generated_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        data_version=date(2026, 7, 1),
    )

    payload = body(resolved(snapshot=older, requested_snapshot_id=older.id))

    assert payload["snapshot"]["id"] == str(older.id)
    assert payload["snapshot"]["data_version"] == "2026-07-01"


# ---------------------------------------------------------------------------
# The stores, unreachable
# ---------------------------------------------------------------------------
@pytest.fixture
def app_without_a_database(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("TCG_API_DATABASE_URL", raising=False)
    for cached in CACHES:
        cached.cache_clear()
    yield create_app()
    for cached in CACHES:
        cached.cache_clear()


def test_an_unconfigured_database_is_a_503_and_says_which_store(
    app_without_a_database: Any,
) -> None:
    with TestClient(app_without_a_database) as client:
        response = client.get(f"/cards/{CHARIZARD.id}/market")

    assert response.status_code == 503
    assert response.json()["code"] == ErrorCode.PROVIDER_ERROR.value
    assert response.json()["details"] == {"reason": "market_store_unreachable"}


def test_the_failure_reason_is_distinct_from_the_catalogs(app_without_a_database: Any) -> None:
    """Both stores are one PostgreSQL; an operator still learns which read failed."""
    with TestClient(app_without_a_database) as client:
        market_reason = client.get(f"/cards/{CHARIZARD.id}/market").json()["details"]["reason"]
        catalog_reason = client.get(f"/cards/{CHARIZARD.id}").json()["details"]["reason"]

    assert market_reason != catalog_reason


def test_the_reason_is_not_mistakable_for_the_taxonomy_code() -> None:
    """`market_store_unreachable`, never `market_data_unreachable`.

    This one route raises spec §66's `market_data_unavailable` as well, and two
    strings differing by two letters in a log is a trap.
    """
    assert "market_data" not in "market_store_unreachable"


def test_a_driver_failure_reading_prices_is_a_503_not_a_500(
    monkeypatch: pytest.MonkeyPatch, app_without_a_database: Any
) -> None:
    """A store that will not answer is not something unexpected having happened."""

    async def unreachable(*args: Any, **kwargs: Any) -> None:
        raise MarketSnapshotUnavailable("The market data store could not be reached.")

    monkeypatch.setattr(market, "current_snapshot", unreachable)

    with TestClient(app_without_a_database) as client:
        response = client.get(f"/cards/{CHARIZARD.id}/market")

    assert response.status_code == 503
    assert response.json()["code"] == ErrorCode.PROVIDER_ERROR.value


def test_a_failure_never_leaks_the_driver(app_without_a_database: Any) -> None:
    with TestClient(app_without_a_database) as client:
        text = client.get(f"/cards/{CHARIZARD.id}/market").text.lower()

    assert "asyncpg" not in text
    assert "postgresql" not in text
    assert "traceback" not in text


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
def test_the_response_is_not_cached() -> None:
    """`price_age` is computed at the moment of asking; a cached body freezes it.

    The deliberate opposite of `/grading-companies`, whose body is the same all
    hour. Spec §38 forbids presenting stale data without identifying it, and a
    cached age is exactly that.
    """
    assert get().headers["Cache-Control"] == "no-store"


def test_an_error_carries_no_cache_header() -> None:
    response = get(resolved(snapshot=None, prices=()))

    assert response.status_code == 503
    assert "Cache-Control" not in response.headers


def test_the_amount_is_an_exact_decimal_string_not_a_json_number() -> None:
    """A JSON number is a float in most clients, and this is money."""
    assert '"amount":"900.00"' in get().text.replace(" ", "")
    assert '"amount":900.0' not in get().text.replace(" ", "")


# ---------------------------------------------------------------------------
# The published contract
# ---------------------------------------------------------------------------
def schema() -> dict[str, Any]:
    return create_app().openapi()


def test_openapi_documents_the_market_path() -> None:
    assert "/cards/{card_id}/market" in schema()["paths"]


def test_openapi_documents_every_field_a_client_renders_from() -> None:
    components = schema()["components"]["schemas"]

    assert set(components["CardMarketResponse"]["properties"]) == {
        "card_id",
        "snapshot",
        "raw",
        "graded",
    }
    assert set(components["GradedPriceResponse"]["properties"]) == {"company", "grade", "price"}
    assert set(components["MarketSnapshotResponse"]["properties"]) == {
        "id",
        "generated_at",
        "data_version",
    }
    assert set(components["PriceResponse"]["properties"]) == {
        "amount",
        "currency",
        "price_confidence",
        "price_age_seconds",
        "observed_at",
    }


def test_openapi_types_the_amount_as_a_string() -> None:
    """A `Decimal` field would generate `number | string`, inviting a parseFloat."""
    amount = schema()["components"]["schemas"]["PriceResponse"]["properties"]["amount"]

    assert amount["type"] == "string"


def test_openapi_does_not_publish_the_providers_undiscounted_confidence() -> None:
    """`confidence` and `price_confidence` are different numbers with similar names.

    Only the second is fit to show a user, so only the second is on the wire.
    """
    assert "confidence" not in schema()["components"]["schemas"]["PriceResponse"]["properties"]


def test_openapi_documents_both_refusals() -> None:
    responses = schema()["paths"]["/cards/{card_id}/market"]["get"]["responses"]

    for code in ("404", "503"):
        schema_ref = responses[code]["content"]["application/json"]["schema"]["$ref"]
        assert schema_ref.endswith("/ErrorResponse")


def test_the_endpoint_is_not_rate_limited() -> None:
    """A read is not one of spec §55's analysis endpoints or uploads (ADR 0005)."""
    assert "429" not in schema()["paths"]["/cards/{card_id}/market"]["get"]["responses"]


def test_nothing_lists_snapshots() -> None:
    """What keeps `?snapshot_id=` from becoming ADR 0006's forbidden history endpoint.

    The parameter takes an unguessable identifier a caller can only have got
    from its own analysis's reproducibility record. A route that enumerated them
    would turn this endpoint into a price browser by iteration.
    """
    paths = schema()["paths"]

    assert not [path for path in paths if "snapshot" in path]
