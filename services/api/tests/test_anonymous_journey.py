"""One anonymous analysis, driven through every endpoint — #250, spec §62.

Every other test of the analysis routes either hands `enqueue_analysis` a list
or moves a row with `UPDATE analyses SET status = ...`, and the worker's tests
drive `_advance` with every step stubbed. Nothing joined the halves: the
journey `POST /analyses → images → run → worker → confirm-card →
economic-configuration → results` had never once been walked through its own
doors. This module walks it, with real photographs in a real object store and
no pipeline step faked — the worker is the real `_advance`, run in-process the
way `test_analyses_endpoint.py`'s `worked()` runs it, because the broker is
#35's concern and there is no Redis in any test job.

It is also where epic #10's sixth item is asserted on the wire before any
screen renders it: every stage that can emit `insufficient_information` —
the gate, identification, the condition, the predictors, the market, the
recommendation — is checked where it lands.

Two services, both real, both required. It imports the CV stack at module
scope on purpose, as `test_datasets_normalization.py` does, and is skipped
unless both of these are set:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000

It carries both the `integration` and the `object_storage` marker, and both
skips: CI's database job has no MinIO and its storage job now has both, so
the module runs exactly where it can.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from numpy.typing import NDArray
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis import jobs
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.storage import get_object_storage
from tcg_shared.storage import StorageKey

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
ENDPOINT_URL = os.environ.get("TCG_API_STORAGE_ENDPOINT_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)
needs_minio = pytest.mark.skipif(
    not ENDPOINT_URL,
    reason="TCG_API_STORAGE_ENDPOINT_URL is unset; no live MinIO to exercise",
)

#: `test_analyses_endpoint.py`'s list: every process-wide cache that would
#: otherwise carry one test's event loop or environment into the next.
CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

CATALOG_VERSION = "pokemon-catalog-v9.9.9"
CARD_ID = uuid.UUID("11111111-1111-5111-8111-111111111111")
SET_ID = uuid.UUID("22222222-2222-5222-8222-222222222222")

#: Spec §65's states in the order a new user's analysis is observed to hold
#: them. `queued` is a transport word, `identifying` is held only while the
#: worker owns the row and `calculating` is passed through inside the
#: configuration's transaction — none of the three is ever polled.
OBSERVED_STATES = (
    "created",
    "uploading",
    "uploaded",
    "awaiting_confirmation",
    "analyzing",
    "completed",
)

#: `test_datasets_normalization.py`'s numbers, copied rather than imported for
#: the reason it gives about `ml/card-detection`'s: a fixture shared across
#: modules is a dependency nobody declared.
HEIGHT, WIDTH = 1600, 1200
CARD = (285, 360, 630, 880)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# The photographs
# ---------------------------------------------------------------------------


def printed(width: int, height: int, tone: int, seed: int) -> NDArray[np.uint8]:
    generator = np.random.default_rng(seed)
    face = np.full((height, width, 3), tone, np.uint8)
    inset = width // 8
    panel = generator.integers(0, 255, (9, 6, 3), dtype=np.uint8)
    art = cv2.resize(panel, (width - 2 * inset, height - 2 * inset), interpolation=cv2.INTER_CUBIC)
    face[inset : height - inset, inset : width - inset] = art
    grain = generator.integers(-10, 10, (height, width, 1))
    lit = np.clip(face.astype(np.int16) * (tone / 255.0) + grain, 0, 255)
    return lit.astype(np.uint8)


def photograph(*, seed: int = 1, face: int = 230, surface: int = 40) -> NDArray[np.uint8]:
    generator = np.random.default_rng(seed + 1000)
    speckle = generator.integers(-8, 9, size=(HEIGHT, WIDTH, 1))
    picture = np.clip(np.full((HEIGHT, WIDTH, 3), surface, np.int16) + speckle, 0, 255).astype(
        np.uint8
    )
    left, top, width, height = CARD
    picture[top : top + height, left : left + width] = printed(width, height, face, seed)
    return picture


def jpeg(picture: NDArray[np.uint8]) -> bytes:
    """What a phone sends: the photograph as a JPEG, not the PNG the normalizer's test uses."""
    return bytes(cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes())


def unusable_photograph() -> bytes:
    """A 64x48 JPEG: below the gate's 640 px short edge, so `unusable` outright."""
    return jpeg(np.full((48, 64, 3), 90, np.uint8))


# ---------------------------------------------------------------------------
# The database, past the API
# ---------------------------------------------------------------------------


def querying(statement: str, **parameters: Any) -> Any:
    async def read() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                result = await connection.execute(sa.text(statement), parameters)
                return result.all()
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


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """Bring the schema up once for the module — and truncate nothing.

    `test_analyses_endpoint.py`'s fixture empties the session tree here; this
    one does not, because everything it writes it deletes again row by row
    (`journey`), and the corpus guard in the root `conftest.py` exists because a
    fixture once could truncate a table that mattered.
    """
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


@pytest.fixture(scope="module", autouse=True)
def seeded(migrated: None) -> None:
    """What a deployment has before its first user: a catalog, a card, the rules.

    All three writes are idempotent and none is undone — a catalog version and
    a grading rules version are immutable by design (their tables refuse a
    DELETE), and a set with one card is harmless in a developer's database.
    The rules go through `tcg-seed-grading-rules`' own writer, so
    `grading_rules_version` is what the command would have left in force.
    """
    if not DATABASE_URL:
        return

    from tcg_api.grading.seed import apply_grading_rules, load_grading_rules

    executing(
        "INSERT INTO sets (id, game, language, set_code, name) "
        "VALUES (:id, 'pokemon', 'en', 'CONF', 'Confirmation Test Set') "
        "ON CONFLICT (id) DO NOTHING",
        id=SET_ID,
    )
    executing(
        "INSERT INTO cards (id, game, language, set_id, card_number, name, variant) "
        "VALUES (:id, 'pokemon', 'en', :set_id, '1/1', 'Confirmation Test Card', 'holo') "
        "ON CONFLICT (id) DO NOTHING",
        id=CARD_ID,
        set_id=SET_ID,
    )
    executing(
        "INSERT INTO card_database_versions "
        "(id, version, source, generated_at, set_count, card_count, external_id_count) "
        "VALUES (:id, :version, 'manual', now(), 0, 0, 0) "
        "ON CONFLICT (version) DO NOTHING",
        id=uuid.uuid5(uuid.NAMESPACE_URL, CATALOG_VERSION),
        version=CATALOG_VERSION,
    )

    async def publish() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            await apply_grading_rules(load_grading_rules(), engine)
        finally:
            await engine.dispose()

    run(publish)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """One anonymous user on one event loop — `test_analyses_endpoint.py`'s fixture.

    No storage override. The upload route reaches the store through a FastAPI
    dependency and the worker through the module-level `get_object_storage()`,
    and only a real store is reachable from both; that is what the
    `object_storage` marker means here.
    """
    for cached in CACHES:
        cached.cache_clear()
    with TestClient(create_app()) as instance:
        yield instance


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """The one seam that is stubbed, and it is transport, not a pipeline step.

    `POST /analyses/{id}/run` publishes to Celery; without a broker it answers
    503 and the row stays `uploaded`. The issue's "no Celery round trip" is
    this: the publish is recorded, the task body is run by `worked()`.
    """
    handed: list[uuid.UUID] = []

    def record(analysis_id: uuid.UUID) -> str:
        handed.append(analysis_id)
        return "job-1"

    monkeypatch.setattr("tcg_api.routers.analyses.enqueue_analysis", record)
    return handed


@pytest.fixture
def journey() -> Iterator[list[str]]:
    """Every analysis a test starts, removed afterwards in FK order.

    Objects first — the sweep's rule (#41), objects before rows — then the
    session, which cascades to the analysis and its images, then the
    configuration the analysis pointed at, whose key is RESTRICT.
    """
    started: list[str] = []
    yield started
    for analysis_id in started:
        rows = querying(
            "SELECT original_uri, normalized_uri FROM images WHERE analysis_id = :id",
            id=uuid.UUID(analysis_id),
        )
        keys = [StorageKey(uri) for row in rows for uri in row if uri]
        if keys:
            get_object_storage.cache_clear()

            async def remove(keys: list[StorageKey] = keys) -> None:
                storage = get_object_storage()
                for key in keys:
                    await storage.delete(key)

            run(remove)
        owner = querying(
            "SELECT session_id, economic_configuration_id FROM analyses WHERE id = :id",
            id=uuid.UUID(analysis_id),
        )
        if not owner:
            continue
        session_id, configuration_id = owner[0]
        executing("DELETE FROM analysis_sessions WHERE id = :id", id=session_id)
        if configuration_id is not None:
            executing("DELETE FROM economic_configurations WHERE id = :id", id=configuration_id)


# ---------------------------------------------------------------------------
# Driving the endpoints
# ---------------------------------------------------------------------------


def worked(analysis_id: str) -> None:
    """Run the job the way a worker would, without a worker.

    The task's function, with a request pushed so `self.request` is populated —
    the same seam `test_analysis_jobs.py` uses. No broker, no eager mode, and no
    `asyncio.run` nested inside the test client's event loop.
    """
    jobs.run_analysis.push_request(retries=0, id="job-1", called_directly=False)
    try:
        jobs.run_analysis.run(analysis_id)
    finally:
        jobs.run_analysis.pop_request()


def send(client: TestClient, analysis_id: str, side: str, body: bytes) -> Any:
    response = client.post(
        f"/analyses/{analysis_id}/images",
        params={"side": side},
        content=body,
        headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def polled(client: TestClient, analysis_id: str) -> Any:
    response = client.get(f"/analyses/{analysis_id}")
    assert response.status_code == 200, response.text
    return response.json()


def is_refusal(value: Any) -> bool:
    """#245's rule: an answer and a refusal are told apart by this key, never by null."""
    return isinstance(value, dict) and "insufficient_information" in value


def probability_mass(distribution: list[dict[str, Any]]) -> float:
    return sum(term["probability"] for term in distribution)


# ---------------------------------------------------------------------------
# The journey
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.object_storage
@requires_postgres
@needs_minio
def test_a_new_user_completes_an_anonymous_analysis(
    client: TestClient, enqueued: list[uuid.UUID], journey: list[str]
) -> None:
    """Spec §69/M9's acceptance, on the wire: the whole journey, every state observed.

    The V1 admission at the end is the assertion, not an obstacle — with the
    declared-uncertainty predictors (ADR 0011) and no market snapshot (ADR 0006)
    a new user today gets `insufficient_information` with its reasons, and this
    pins that the reasons arrive rather than a fabricated verdict.
    """
    from tcg_api.analysis.condition import CONDITION_VERSION
    from tcg_api.analysis.grading import GRADING_VERSION
    from tcg_api.grading.seed import load_grading_rules

    observed: list[str] = []

    # 1. A session and an analysis.
    created = client.post("/analyses")
    assert created.status_code == 201, created.text
    analysis_id: str = created.json()["id"]
    journey.append(analysis_id)
    assert "tcg_session" in client.cookies
    observed.append(created.json()["status"])

    opened = polled(client, analysis_id)
    assert opened["images"] == []
    assert opened["card_id"] is None
    assert opened["reproducibility"].pop("image_sha256") == {}
    assert all(value is None for value in opened["reproducibility"].values())

    # 2. Two photographs — two different ones, so the digests tell them apart;
    # the second upload is what makes the analysis runnable.
    front = send(client, analysis_id, "front", jpeg(photograph(seed=1)))
    observed.append(front["analysis_status"])
    back = send(client, analysis_id, "back", jpeg(photograph(seed=2)))
    observed.append(back["analysis_status"])
    digests = {"front": front["sha256"], "back": back["sha256"]}
    assert digests["front"] != digests["back"]

    # 3. The run is acknowledged as `queued`, a word no row ever holds.
    queued = client.post(f"/analyses/{analysis_id}/run")
    assert queued.status_code == 202, queued.text
    assert queued.json() == {"analysis_id": analysis_id, "status": "queued"}
    assert enqueued == [uuid.UUID(analysis_id)]
    assert polled(client, analysis_id)["status"] == "uploaded"

    # 4. The worker: gate, detection, normalization, condition, prediction.
    worked(analysis_id)

    gated = polled(client, analysis_id)
    observed.append(gated["status"])
    assert gated["completed_at"] is None
    # Identification emits no confidence, by absence (#91): the poll carries
    # the card the user confirmed or null, and no other field about it.
    assert gated["card_id"] is None
    assert set(gated) == {
        "id",
        "status",
        "created_at",
        "completed_at",
        "card_id",
        "images",
        "reproducibility",
    }

    # The gate's verdict is on the wire per side, and it let the card through.
    sides = {image["side"]: image for image in gated["images"]}
    assert set(sides) == {"front", "back"}
    for image in sides.values():
        assert image["quality_status"] == "good", image
        assert image["quality_score"] is not None and 0.0 < image["quality_score"] <= 1.0
        assert len(image["findings"]) == 11
        undetermined = [f["condition"] for f in image["findings"] if f["verdict"] == "undetermined"]
        assert undetermined == [], undetermined

    # Spec §57's record, written at the claim. `market_snapshot_id` is null
    # because nothing has ever generated a snapshot, which is a fact about this
    # deployment rather than a gap in the record.
    record = gated["reproducibility"]
    assert record["application_version"] is not None
    assert record["model_bundle_version"] == f"{CONDITION_VERSION}+{GRADING_VERSION}"
    # The published catalog, as `GET /catalog/version` reports it — a developer's
    # database may hold a newer one than the module seeded, and that is the one.
    assert record["card_database_version"] == client.get("/catalog/version").json()["version"]
    in_force = sorted(load_grading_rules(), key=lambda rules: rules.company)
    assert [rules.company for rules in in_force] == ["bgs", "psa", "tag"]
    assert record["grading_rules_version"] == "+".join(rules.version for rules in in_force)
    assert record["market_snapshot_id"] is None
    assert record["economic_configuration_id"] is None
    assert record["image_sha256"] == digests

    # 5. The user names the card; the analysis moves on.
    confirmed = client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)})
    assert confirmed.status_code == 200, confirmed.text
    named = polled(client, analysis_id)
    observed.append(named["status"])
    assert named["card_id"] == str(CARD_ID)

    # 6. The economics are the last input, and recording them completes the analysis.
    configured = client.post(
        f"/analyses/{analysis_id}/economic-configuration",
        json={
            "acquisition_cost": "100.00",
            "grading_companies": ["psa", "tag", "bgs"],
            "optimization_mode": "expected_profit",
        },
    )
    assert configured.status_code == 201, configured.text
    done = polled(client, analysis_id)
    observed.append(done["status"])
    assert done["completed_at"] is not None
    assert done["reproducibility"]["economic_configuration_id"] == configured.json()["id"]
    assert tuple(observed) == OBSERVED_STATES

    # 7. The results.
    response = client.get(f"/analyses/{analysis_id}/results")
    assert response.status_code == 200, response.text
    results = response.json()
    assert results["status"] == "completed"
    assert results["card_id"] == str(CARD_ID)
    assert results["economic_configuration"] is not None
    assert results["market_snapshot"] is None

    # Every company predicted, in the configuration's order — none refused.
    assert [company["company"] for company in results["companies"]] == ["psa", "tag", "bgs"]
    assert results["refused"] == {}

    # Each distribution is §63-valid over that company's own ladder, as
    # `GET /grading-companies` publishes it: BGS's carries 9.5, the others' do not.
    ladders = {
        company["company"]: company["grades"]
        for company in client.get("/grading-companies").json()["companies"]
    }
    for company in results["companies"]:
        distribution = company["grade_distribution"]
        assert [term["grade"] for term in distribution] == ladders[company["company"]]
        assert all(0.0 <= term["probability"] <= 1.0 for term in distribution)
        assert abs(probability_mass(distribution) - 1.0) < 1e-6
    assert "9.5" in ladders["bgs"]
    assert "9.5" not in ladders["psa"] and "9.5" not in ladders["tag"]

    # The market's answer: no snapshot, so every figure is present-and-null
    # beside the reason nothing could be priced — never zero, never omitted.
    for company in results["companies"]:
        assert company["expected_graded_value"] is None
        assert company["expected_graded_value_reason"] == "no_graded_price_available"
        assert company["incremental_grading_decision"] is None
        assert company["incremental_reason"] == "no_raw_price_available"
        assert company["incremental_roi"] is None
        assert company["incremental_roi_reason"] == "no_raw_price_available"
        assert company["investment_return"] is None
        assert company["investment_reason"] == "no_graded_price_available"
        assert company["investment_roi"] is None
        assert company["investment_roi_reason"] == "no_graded_price_available"

    # The recommendation: asked, and honestly declined, with its reason. With
    # nothing priced no company can be ranked, and the engine says so before it
    # ever weighs a grade confidence — so that gate is `null`, "never reached",
    # not a number. The admission's two figures still travel: every model's
    # declared 0.35 (ADR 0011) beside the configuration's 0.5 threshold (#64).
    recommendation = results["recommendation"]
    assert recommendation is not None
    assert recommendation["recommended_action"] == "insufficient_information"
    assert recommendation["recommended_company"] is None
    assert recommendation["reason"] == {
        "code": "no_company_can_be_ranked",
        "figure": "ranked_companies",
        "value": None,
        "threshold": None,
    }
    assert [gate["code"] for gate in recommendation["failed_gates"]] == ["no_company_can_be_ranked"]
    assert recommendation["grade_confidence"] is None
    assert recommendation["figure_confidence"] is None
    # §44's third confidence source is the weakest photograph, and with nothing
    # else measured it is the whole confidence.
    weakest = min(image["quality_score"] for image in sides.values())
    assert recommendation["image_quality"] == weakest
    assert recommendation["confidence"] == weakest
    assert recommendation["comparison"] is None
    assert recommendation["comparison_reason"] == "no_company_can_be_ranked"
    assert all(company["distribution_confidence"] == 0.35 for company in results["companies"])
    assert results["economic_configuration"]["thresholds"]["minimum_grade_confidence"] == 0.5

    # The condition the predictors read: an assessment, per axis its answer or
    # its refusal, and the V1 shape #249's record describes.
    condition = results["condition"]
    assert condition is not None
    assert condition["version"] == CONDITION_VERSION
    assert condition["confidence"] is not None and 0.0 < condition["confidence"] <= 1.0
    assert condition["eye_appeal"] == {"insufficient_information": "eye_appeal_not_measured_in_v1"}
    assert condition["manufacturing_defects"] == {
        "insufficient_information": "manufacturing_classes_not_assessed"
    }
    centering = condition["centering"]
    assert is_refusal(centering) or set(centering) == {
        "front_horizontal",
        "front_vertical",
        "back_horizontal",
        "back_vertical",
        "confidence",
    }
    for side in ("front", "back"):
        for axis in ("corners", "edges"):
            regions = condition[axis][side]
            assert not is_refusal(regions), (axis, side, regions)
            assert len(regions) == 4
            for finding in regions.values():
                assert finding["label"] in {"clean", "whitening", "unknown"}
        surface = condition["surface"][side]
        assert not is_refusal(surface), (side, surface)
        assert len(surface["not_assessed"]) == 9
        assert all(is_refusal(reason) for reason in surface["not_assessed"].values())
        # The V1 analyzer segments every stain it sees as its own finding;
        # the screen counts them (#249), and the wire carries every one.
        assert surface["findings"], side
        for finding in surface["findings"]:
            assert finding["type"] in {"stain", "scuff"}
            assert finding["side"] == side


@pytest.mark.integration
@pytest.mark.object_storage
@requires_postgres
@needs_minio
def test_an_unusable_photograph_fails_the_analysis_honestly(
    client: TestClient, enqueued: list[uuid.UUID], journey: list[str]
) -> None:
    """Spec §19's `unusable` → stop, observed through the same doors.

    The gate's refusal lands as `quality_status` per side; `confirm-card`
    names the sides; the configuration is refused because the analysis is not
    `analyzing`; and the results still answer — nothing asked, nothing assessed,
    every collection empty and every nullable field null.
    """
    created = client.post("/analyses").json()
    analysis_id: str = created["id"]
    journey.append(analysis_id)

    picture = unusable_photograph()
    send(client, analysis_id, "front", picture)
    assert send(client, analysis_id, "back", picture)["analysis_status"] == "uploaded"
    assert client.post(f"/analyses/{analysis_id}/run").status_code == 202

    worked(analysis_id)

    failed = polled(client, analysis_id)
    assert failed["status"] == "failed"
    assert failed["completed_at"] is not None
    assert failed["card_id"] is None
    assert {image["side"]: image["quality_status"] for image in failed["images"]} == {
        "front": "unusable",
        "back": "unusable",
    }
    # The record was written at the claim, before the gate spoke.
    assert failed["reproducibility"]["application_version"] is not None
    assert failed["reproducibility"]["model_bundle_version"] is not None

    refused = client.post(f"/analyses/{analysis_id}/confirm-card", json={"card_id": str(CARD_ID)})
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "image_quality_failure"
    assert refused.json()["details"]["sides"] == ["back", "front"]

    unconfigurable = client.post(
        f"/analyses/{analysis_id}/economic-configuration",
        json={"grading_companies": ["psa"], "optimization_mode": "expected_profit"},
    )
    assert unconfigurable.status_code == 409, unconfigurable.text

    response = client.get(f"/analyses/{analysis_id}/results")
    assert response.status_code == 200, response.text
    results = response.json()
    assert results["status"] == "failed"
    assert results["card_id"] is None
    assert results["economic_configuration"] is None
    assert results["market_snapshot"] is None
    assert results["condition"] is None
    assert results["companies"] == []
    assert results["refused"] == {}
    assert results["recommendation"] is None
