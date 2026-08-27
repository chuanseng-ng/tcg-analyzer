"""`POST /analyses/{id}/economic-configuration` — #65.

Against real PostgreSQL, on `test_analyses_endpoint.py`'s terms and for its
reasons: what this endpoint has to get right is that one anonymous user cannot
configure another's analysis, that a configuration is written exactly once, and
that an absent acquisition cost survives a round trip through a nullable column
as an absence. All three live in the database, and a stub answering them in
Python would be testing the stub.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg

The validation tests at the foot need no database — a 422 is raised before any
statement runs — but they carry the marker anyway rather than splitting the
file: the client fixture builds an application either way.
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
from tcg_api.storage import get_object_storage
from tcg_economic_engine import DEFAULT_THRESHOLDS, CostConfiguration

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

#: A configuration that says everything, so a round trip has something to prove.
FULL = {
    "acquisition_cost": "120.00",
    "costs": {
        "grading_fee": "40.00",
        "outbound_shipping": "30.00",
        "return_shipping": "30.00",
        "insurance": "5.00",
        "miscellaneous": "1.50",
        "selling_fee": {"rate": "0.1250", "flat": "2.00"},
    },
    "grading_companies": ["psa", "bgs"],
    "optimization_mode": "expected_profit",
}

#: The least a caller may say: which companies, and what they are optimizing for.
MINIMAL = {"grading_companies": ["psa"], "optimization_mode": "roi"}


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

    async def empty() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text("TRUNCATE analysis_sessions, economic_configurations CASCADE")
                )
        finally:
            await engine.dispose()

    run(empty)


def querying(statement: str, **parameters: Any) -> Any:
    async def read() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                result = await connection.execute(sa.text(statement), parameters)
                return result.scalar()
        finally:
            await engine.dispose()

    return run(read)


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
    """One anonymous user, with their own cookie jar — see `test_analyses_endpoint.py`."""
    for cached in CACHES:
        cached.cache_clear()
    with TestClient(create_app()) as instance:
        yield instance


def configurable(client: TestClient) -> str:
    """An analysis in the one state a configuration may be recorded from.

    Forced with an UPDATE rather than driven through the pipeline: getting there
    honestly needs two uploads, a worker and a card to confirm, none of which
    this endpoint depends on. `test_analyses_endpoint.py` sets the same
    precedent for `run` and `confirm-card`.
    """
    analysis_id = client.post("/analyses").json()["id"]
    executing("UPDATE analyses SET status = 'analyzing' WHERE id = :id", id=uuid.UUID(analysis_id))
    return str(analysis_id)


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
def test_a_configuration_round_trips(client: TestClient) -> None:
    """Everything the caller said comes back, spelled the way it was stored."""
    analysis_id = configurable(client)

    response = client.post(f"/analyses/{analysis_id}/economic-configuration", json=FULL)

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["currency"] == "SGD"
    assert body["acquisition_cost"] == "120.00"
    assert body["costs"]["grading_fee"] == "40.00"
    assert body["costs"]["insurance"] == "5.00"
    assert body["costs"]["miscellaneous"] == "1.50"
    assert body["costs"]["selling_fee"] == {"rate": "0.1250", "flat": "2.00"}
    assert body["grading_companies"] == ["psa", "bgs"]
    assert body["optimization_mode"] == "expected_profit"


@pytest.mark.integration
@requires_postgres
def test_the_configuration_is_attached_to_the_analysis(client: TestClient) -> None:
    """Spec §57: the analysis records which configuration it was computed under."""
    analysis_id = configurable(client)

    stored = client.post(f"/analyses/{analysis_id}/economic-configuration", json=FULL).json()

    linked = querying(
        "SELECT economic_configuration_id FROM analyses WHERE id = :id",
        id=uuid.UUID(analysis_id),
    )
    assert str(linked) == stored["id"]


@pytest.mark.integration
@requires_postgres
def test_the_costs_default_to_the_engines_own_placeholders(client: TestClient) -> None:
    """One source for the defaults, so `apps/web` never has a second copy.

    They are non-zero on purpose: an all-zero configuration reports grading as
    costless and tilts every recommendation toward *grade*.
    """
    analysis_id = configurable(client)

    body = client.post(f"/analyses/{analysis_id}/economic-configuration", json=MINIMAL).json()

    defaults = CostConfiguration()
    assert Decimal(body["costs"]["grading_fee"]) == defaults.grading_fee.amount
    assert Decimal(body["costs"]["outbound_shipping"]) == defaults.outbound_shipping.amount
    assert Decimal(body["costs"]["return_shipping"]) == defaults.return_shipping.amount
    assert Decimal(body["costs"]["insurance"]) == defaults.insurance.amount
    assert Decimal(body["costs"]["miscellaneous"]) == defaults.miscellaneous.amount
    assert Decimal(body["costs"]["selling_fee"]["rate"]) == defaults.selling_fee.rate
    assert Decimal(body["costs"]["selling_fee"]["flat"]) == defaults.selling_fee.flat.amount
    # Non-zero on purpose: an all-zero configuration reports grading as costless.
    assert defaults.grading_fee.amount > 0


# ---------------------------------------------------------------------------
# Absent is not zero — spec §45
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
def test_an_absent_acquisition_cost_is_null_and_not_zero(client: TestClient) -> None:
    """§45's whole point, and #91's "Not measured" is never `0%` pointed at money."""
    analysis_id = configurable(client)

    body = client.post(f"/analyses/{analysis_id}/economic-configuration", json=MINIMAL).json()

    assert body["acquisition_cost"] is None
    assert (
        querying(
            "SELECT acquisition_cost FROM economic_configurations WHERE id = :id",
            id=uuid.UUID(body["id"]),
        )
        is None
    )


@pytest.mark.integration
@requires_postgres
def test_a_zero_acquisition_cost_is_a_real_answer(client: TestClient) -> None:
    """A raffle win. It must be distinguishable from "I don't remember"."""
    analysis_id = configurable(client)

    body = client.post(
        f"/analyses/{analysis_id}/economic-configuration",
        json={**MINIMAL, "acquisition_cost": "0.00"},
    ).json()

    assert body["acquisition_cost"] == "0.00"
    assert querying(
        "SELECT acquisition_cost FROM economic_configurations WHERE id = :id",
        id=uuid.UUID(body["id"]),
    ) == Decimal("0.00")


@pytest.mark.integration
@requires_postgres
def test_nothing_infers_an_acquisition_cost(client: TestClient) -> None:
    """§45: "Do not infer it." Not from the costs, and not from a market price."""
    analysis_id = configurable(client)

    body = client.post(f"/analyses/{analysis_id}/economic-configuration", json=MINIMAL).json()

    assert body["acquisition_cost"] is None


# ---------------------------------------------------------------------------
# Thresholds — stored, reported, never accepted
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
def test_the_thresholds_are_stored_from_the_engines_defaults(client: TestClient) -> None:
    """#64's five, recorded so a recommendation stays reproducible when they move."""
    analysis_id = configurable(client)

    thresholds = client.post(
        f"/analyses/{analysis_id}/economic-configuration", json=MINIMAL
    ).json()["thresholds"]

    assert thresholds["minimum_image_quality"] == DEFAULT_THRESHOLDS.minimum_image_quality.value
    assert thresholds["minimum_grade_confidence"] == (
        DEFAULT_THRESHOLDS.minimum_grade_confidence.value
    )
    assert thresholds["minimum_figure_confidence"] == (
        DEFAULT_THRESHOLDS.minimum_figure_confidence.value
    )
    assert thresholds["maximum_unpriced_probability"] == (
        DEFAULT_THRESHOLDS.maximum_unpriced_probability
    )
    assert Decimal(thresholds["minimum_incremental_profit"]) == (
        DEFAULT_THRESHOLDS.minimum_incremental_profit.amount
    )


@pytest.mark.integration
@requires_postgres
def test_a_client_cannot_choose_its_own_thresholds(client: TestClient) -> None:
    """A policy is not a card's costs, and gating your own recommendation is not a setting."""
    analysis_id = configurable(client)

    body = client.post(
        f"/analyses/{analysis_id}/economic-configuration",
        json={**MINIMAL, "thresholds": {"minimum_incremental_profit": "0.00"}},
    ).json()

    assert Decimal(body["thresholds"]["minimum_incremental_profit"]) == (
        DEFAULT_THRESHOLDS.minimum_incremental_profit.amount
    )


# ---------------------------------------------------------------------------
# Written once
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
def test_a_second_configuration_is_refused(client: TestClient) -> None:
    """Immutable: re-pricing the card is a new analysis, not an edit."""
    analysis_id = configurable(client)
    first = client.post(f"/analyses/{analysis_id}/economic-configuration", json=FULL)

    second = client.post(f"/analyses/{analysis_id}/economic-configuration", json=MINIMAL)

    assert first.status_code == 201
    assert second.status_code == 409
    assert (
        str(
            querying(
                "SELECT economic_configuration_id FROM analyses WHERE id = :id",
                id=uuid.UUID(analysis_id),
            )
        )
        == first.json()["id"]
    )


@pytest.mark.integration
@requires_postgres
def test_a_refused_second_configuration_leaves_no_row_behind(client: TestClient) -> None:
    """The loser's INSERT is dropped by the rollback rather than by a delete."""
    analysis_id = configurable(client)
    client.post(f"/analyses/{analysis_id}/economic-configuration", json=FULL)

    before = querying("SELECT count(*) FROM economic_configurations")
    client.post(f"/analyses/{analysis_id}/economic-configuration", json=MINIMAL)

    assert querying("SELECT count(*) FROM economic_configurations") == before


@pytest.mark.integration
@requires_postgres
def test_the_stored_row_cannot_be_updated(client: TestClient) -> None:
    """The database refuses it, not merely this service — spec §57's immutability."""
    analysis_id = configurable(client)
    stored = client.post(f"/analyses/{analysis_id}/economic-configuration", json=FULL).json()

    with pytest.raises(Exception, match="immutable"):
        executing(
            "UPDATE economic_configurations SET grading_fee = 999 WHERE id = :id",
            id=uuid.UUID(stored["id"]),
        )


# ---------------------------------------------------------------------------
# When it may be recorded
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
@pytest.mark.parametrize("state", ["created", "uploaded", "awaiting_confirmation", "completed"])
def test_only_an_analysing_analysis_takes_a_configuration(client: TestClient, state: str) -> None:
    """Spec §5 puts this step after confirmation; §65 gives that step its state."""
    analysis_id = client.post("/analyses").json()["id"]
    executing(
        "UPDATE analyses SET status = :state, completed_at = "
        "CASE WHEN :state IN ('completed', 'failed') THEN now() END WHERE id = :id",
        state=state,
        id=uuid.UUID(analysis_id),
    )

    response = client.post(f"/analyses/{analysis_id}/economic-configuration", json=MINIMAL)

    assert response.status_code == 409
    assert state in response.json()["detail"]


@pytest.mark.integration
@requires_postgres
def test_an_unknown_analysis_is_the_same_404_as_someone_elses(client: TestClient) -> None:
    """Four misses, one answer: a caller must not learn whether an id exists."""
    analysis_id = configurable(client)
    client.cookies.clear()

    unknown = client.post(f"/analyses/{uuid.uuid4()}/economic-configuration", json=MINIMAL)
    someone_elses = client.post(f"/analyses/{analysis_id}/economic-configuration", json=MINIMAL)

    assert unknown.status_code == someone_elses.status_code == 404
    assert unknown.json() == someone_elses.json()


@pytest.mark.integration
@requires_postgres
def test_a_malformed_analysis_id_is_a_422(client: TestClient) -> None:
    assert (
        client.post("/analyses/not-a-uuid/economic-configuration", json=MINIMAL).status_code == 422
    )


# ---------------------------------------------------------------------------
# Validation — the engine's rules, answered as FastAPI's own 422
# ---------------------------------------------------------------------------


@pytest.mark.integration
@requires_postgres
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({**MINIMAL, "costs": {"grading_fee": "-1.00"}}, "negative"),
        ({**MINIMAL, "costs": {"selling_fee": {"rate": "10"}}}, "rate"),
        ({**MINIMAL, "costs": {"selling_fee": {"rate": "-0.1"}}}, "rate"),
        ({**MINIMAL, "grading_companies": ["cgc"]}, "unsupported grading companies"),
        ({**MINIMAL, "grading_companies": ["psa", "psa"]}, "at most once"),
        ({**MINIMAL, "grading_companies": []}, ""),
        ({**MINIMAL, "optimization_mode": "vibes"}, ""),
        ({**MINIMAL, "acquisition_cost": "-5.00"}, "negative"),
        ({**MINIMAL, "costs": {"grading_fee": 40.0}}, "decimal string"),
    ],
)
def test_a_malformed_configuration_is_refused_with_a_reason(
    client: TestClient, body: dict[str, Any], expected: str
) -> None:
    """422, FastAPI's own: spec §66 has no code meaning "you sent something malformed".

    A percentage where a proportion is meant is the one worth naming: `"10"`
    read as a rate charges a fee a hundred times too large, on the single figure
    every recommendation turns on.
    """
    analysis_id = configurable(client)

    response = client.post(f"/analyses/{analysis_id}/economic-configuration", json=body)

    assert response.status_code == 422
    assert expected in response.text


@pytest.mark.integration
@requires_postgres
def test_a_refused_configuration_writes_nothing(client: TestClient) -> None:
    """Validation runs before any statement, so a 422 cannot leave a half-row."""
    analysis_id = configurable(client)
    before = querying("SELECT count(*) FROM economic_configurations")

    client.post(
        f"/analyses/{analysis_id}/economic-configuration",
        json={**MINIMAL, "optimization_mode": "vibes"},
    )

    assert querying("SELECT count(*) FROM economic_configurations") == before
    assert (
        querying(
            "SELECT economic_configuration_id FROM analyses WHERE id = :id",
            id=uuid.UUID(analysis_id),
        )
        is None
    )


@pytest.mark.integration
@requires_postgres
def test_every_supported_optimization_mode_is_accepted(client: TestClient) -> None:
    """Spec §43's five, and the endpoint must not know a shorter list than the engine."""
    from tcg_economic_engine import STRATEGIES

    for mode in STRATEGIES:
        analysis_id = configurable(client)
        response = client.post(
            f"/analyses/{analysis_id}/economic-configuration",
            json={**MINIMAL, "optimization_mode": mode},
        )
        assert response.status_code == 201, mode


@pytest.mark.integration
@requires_postgres
def test_every_supported_grading_company_is_accepted(client: TestClient) -> None:
    """The same source `GET /grading-companies` serves, so the two cannot disagree."""
    from tcg_grading_companies.companies import ADAPTERS

    analysis_id = configurable(client)
    response = client.post(
        f"/analyses/{analysis_id}/economic-configuration",
        json={**MINIMAL, "grading_companies": sorted(ADAPTERS)},
    )

    assert response.status_code == 201
    assert response.json()["grading_companies"] == sorted(ADAPTERS)
