"""The job queue's configuration and failure handling — issue #35.

No broker and no database. Everything here is either a fact about how the Celery
application is configured or a fact about what the task does when the work
raises, and both are reachable without any infrastructure at all — which is the
point: the security properties below must hold in CI, where there is no Redis.

The one test worth reading twice is `test_a_pickle_message_is_refused`. A Celery
worker willing to deserialize pickle from a broker an attacker can write to is
arbitrary code execution, and it is the best-known attack on this stack. The
`python-background-jobs` skill's example configuration omits every serializer
setting, which is how a worker ends up accepting it by default.
"""

from __future__ import annotations

import asyncio
import pickle
import sys
import types
import uuid
from typing import Any

import pytest
from celery.exceptions import Retry
from kombu.exceptions import ContentDisallowed
from kombu.serialization import dumps, loads, prepare_accept_content
from structlog.testing import CapturingLogger
from tcg_api.analysis import jobs
from tcg_api.config import REDIS_URL_ENV_VAR, get_settings
from tcg_api.version import application_version
from tcg_domain.analysis import AnalysisStatus, QualityStatus

BROKER = "redis://:local@localhost:6379/0"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A Celery application built against a broker URL nothing connects to.

    Both caches are cleared on the way in *and* out: `get_settings` and
    `get_celery_app` are process-wide, and an application carrying one test's
    broker into the next is exactly the kind of ordering dependency that fails
    only in CI.
    """
    monkeypatch.setenv(REDIS_URL_ENV_VAR, BROKER)
    get_settings.cache_clear()
    jobs.get_celery_app.cache_clear()
    try:
        yield jobs.get_celery_app()
    finally:
        get_settings.cache_clear()
        jobs.get_celery_app.cache_clear()


# ---------------------------------------------------------------------------
# Serialization — rule 1 of the hardening rules
# ---------------------------------------------------------------------------


def test_only_json_is_accepted(configured: Any) -> None:
    """All three settings, because any one of them left open is enough.

    `accept_content` alone still lets this process *send* pickle;
    `task_serializer` alone still lets it *receive* pickle.
    """
    assert configured.conf.task_serializer == "json"
    assert configured.conf.result_serializer == "json"
    assert configured.conf.accept_content == ["json"]


def test_a_pickle_message_is_refused(configured: Any) -> None:
    """The attack this configuration exists to stop, executed against it.

    `prepare_accept_content` is what a worker runs `accept_content` through
    before it decodes anything, so this refusal is the worker's own and not a
    re-implementation of it.
    """
    payload = pickle.dumps({"analysis_id": str(uuid.uuid4())})

    with pytest.raises(ContentDisallowed):
        loads(
            payload,
            "application/x-python-serialize",
            "binary",
            accept=prepare_accept_content(configured.conf.accept_content),
        )


def test_a_json_message_is_accepted(configured: Any) -> None:
    """Guard the guard: without this, refusing *everything* would pass above."""
    analysis_id = str(uuid.uuid4())
    content_type, encoding, payload = dumps({"analysis_id": analysis_id}, serializer="json")

    accept = prepare_accept_content(configured.conf.accept_content)

    assert loads(payload, content_type, encoding, accept=accept) == {"analysis_id": analysis_id}


# ---------------------------------------------------------------------------
# The broker — rule 2
# ---------------------------------------------------------------------------


def test_the_broker_url_comes_from_the_environment(configured: Any) -> None:
    """Never a default. A default is how an unauthenticated broker goes unnoticed."""
    assert configured.conf.broker_url == BROKER


def test_an_unconfigured_broker_names_the_variable_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REDIS_URL_ENV_VAR, raising=False)
    get_settings.cache_clear()
    jobs.get_celery_app.cache_clear()
    try:
        with pytest.raises(RuntimeError, match=REDIS_URL_ENV_VAR):
            jobs.get_celery_app()
    finally:
        get_settings.cache_clear()
        jobs.get_celery_app.cache_clear()


def test_enqueuing_without_a_broker_is_a_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So the HTTP layer answers 503 rather than 500.

    An unconfigured queue and an unreachable one are the same answer to a
    caller: this deployment cannot run your analysis right now.
    """
    monkeypatch.delenv(REDIS_URL_ENV_VAR, raising=False)
    get_settings.cache_clear()
    jobs.get_celery_app.cache_clear()
    try:
        with pytest.raises(jobs.JobQueueUnavailable):
            jobs.enqueue_analysis(uuid.uuid4())
    finally:
        get_settings.cache_clear()
        jobs.get_celery_app.cache_clear()


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def test_late_acknowledgement_and_a_prefetch_of_one_are_one_decision(configured: Any) -> None:
    """Acking after the work makes delivery at-least-once, which is the safe half.

    It is only safe because a repeat delivery is a no-op — see
    `test_analysis_state.py`. The prefetch is the other half: with late acks, a
    worker sitting on a batch it has not started is a batch nobody else can run.
    """
    assert configured.conf.task_acks_late is True
    assert configured.conf.worker_prefetch_multiplier == 1


def test_publishing_fails_fast_rather_than_retrying_inside_a_request(configured: Any) -> None:
    """Celery's default policy is right for a worker and wrong for an HTTP caller."""
    policy = configured.conf.task_publish_retry_policy

    assert policy["max_retries"] <= 2
    assert policy["interval_max"] <= 1


def test_the_task_is_registered_under_its_wire_name(configured: Any) -> None:
    """The name is a contract between two processes; renaming the module must not move it."""
    assert jobs.RUN_ANALYSIS in configured.tasks
    assert configured.conf.task_default_queue == jobs.QUEUE


def test_celery_does_not_replace_the_logging_configuration(configured: Any) -> None:
    """Otherwise the dead-letter record is a dict repr inside a log message.

    A worker hijacking the root logger is the default, and it silently undoes
    the structlog pipeline `worker.py` installs — which matters precisely for
    the one line anything downstream is meant to be able to parse.
    """
    assert configured.conf.worker_hijack_root_logger is False


def test_there_is_no_result_backend(configured: Any) -> None:
    """Status is polled from PostgreSQL. A second store of it is a second answer."""
    assert not configured.conf.result_backend


# ---------------------------------------------------------------------------
# The retention sweep's schedule — issue #41, spec §54
# ---------------------------------------------------------------------------
def test_the_retention_sweep_is_scheduled(configured: Any) -> None:
    """Spec §54 needs the sweep to run without anybody remembering to run it.

    The schedule lives in this configuration rather than in the Compose command
    so that a deployment which starts a worker gets retention with it; `--beat`
    only decides which process the scheduler runs in.
    """
    entry = configured.conf.beat_schedule["purge-expired-sessions"]

    assert entry["task"] == jobs.PURGE_EXPIRED
    assert entry["options"]["queue"] == jobs.QUEUE
    assert 0 < entry["schedule"] <= 24 * 60 * 60


def test_the_sweep_is_registered_under_its_wire_name(configured: Any) -> None:
    """`celery call tcg_api.analysis.purge_expired` is how one is run by hand."""
    assert jobs.PURGE_EXPIRED in configured.tasks


def test_the_sweep_is_not_retried(configured: Any) -> None:
    """The next tick is a gentler retry than any backoff, and the rows stay due."""
    assert configured.tasks[jobs.PURGE_EXPIRED].max_retries == 0


# ---------------------------------------------------------------------------
# Retry and the dead-letter path — rule 3
# ---------------------------------------------------------------------------


async def failing(_: Any) -> bool:
    raise ConnectionError("the store is down")


async def already_done(_: Any) -> bool:
    """What `_advance` answers when the analysis has already been claimed."""
    return False


@pytest.mark.parametrize("retries", [0, 1, 2], ids=["first", "second", "third"])
def test_a_failing_run_is_retried_with_a_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch, configured: Any, retries: int
) -> None:
    """With a computed backoff, because this task catches its own exceptions.

    `retry_backoff` is only consulted by the wrapper `autoretry_for` installs,
    and this task does not use it — so the countdown has to be asked for, and
    Celery's untouched default is `default_retry_delay`, three whole minutes.
    The bound below is what distinguishes the two.

    It is a *range* rather than a value because the jitter is full: the wait is
    drawn uniformly from zero to the exponential ceiling, which is the point.
    The usual reason several jobs fail at once is that one dependency is down,
    and retrying them all on the same schedule is how a recovering database gets
    knocked over a second time.
    """
    monkeypatch.setattr(jobs, "_advance", failing)
    # `called_directly` is what tells Celery this is a worker and not a bare
    # function call; without it `retry` re-raises the original by design, and
    # the test would be asserting against a path a worker never takes.
    jobs.run_analysis.push_request(
        retries=retries, id="job-1", called_directly=False, is_eager=True
    )
    try:
        with pytest.raises(Retry) as raised:
            jobs.run_analysis.run(str(uuid.uuid4()))
    finally:
        jobs.run_analysis.pop_request()

    ceiling = min(jobs.RETRY_BACKOFF_SECONDS * 2**retries, jobs.RETRY_BACKOFF_MAX_SECONDS)
    assert 0 <= raised.value.when <= ceiling


def test_the_last_attempt_is_dead_lettered_and_the_analysis_fails(
    monkeypatch: pytest.MonkeyPatch, configured: Any
) -> None:
    """And the record carries the job, the error and the count — nothing else.

    Spec §54: an analysis payload references photographs of somebody's card,
    hands and living room. A dead-letter record holding one indefinitely so that
    a job nobody re-drives could theoretically be re-driven is not a trade this
    project makes, so the assertion below is on what is *absent*.
    """
    failed: list[uuid.UUID] = []
    recorder = CapturingLogger()
    monkeypatch.setattr(jobs, "_advance", failing)
    monkeypatch.setattr(jobs, "_fail_quietly", failed.append)
    # The module's own logger, replaced. `capture_logs` reconfigures structlog
    # globally, which does nothing to a logger another test has already caused
    # to be cached — and the API's app factory configures logging on creation.
    monkeypatch.setattr(jobs, "logger", recorder)

    analysis_id = uuid.uuid4()
    jobs.run_analysis.push_request(retries=jobs.MAX_RETRIES, id="job-2", called_directly=False)
    try:
        with pytest.raises(ConnectionError):
            jobs.run_analysis.run(str(analysis_id))
    finally:
        jobs.run_analysis.pop_request()

    assert failed == [analysis_id]
    record = next(call for call in recorder.calls if call.args == ("analysis.dead_lettered",))
    assert record.method_name == "error"
    assert record.kwargs == {
        "job_id": "job-2",
        "error": "ConnectionError",
        "attempts": jobs.MAX_RETRIES + 1,
    }


def test_a_delivery_with_nothing_to_do_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, configured: Any
) -> None:
    """The at-least-once half of the bargain: a repeat delivery returns quietly."""
    recorder = CapturingLogger()
    monkeypatch.setattr(jobs, "_advance", already_done)
    monkeypatch.setattr(jobs, "logger", recorder)
    jobs.run_analysis.push_request(retries=0, id="job-3", called_directly=False)
    try:
        assert jobs.run_analysis.run(str(uuid.uuid4())) is None
    finally:
        jobs.run_analysis.pop_request()

    assert [call.args[0] for call in recorder.calls] == ["analysis.job_ignored"]


# ---------------------------------------------------------------------------
# The image-quality gate's place in a run — issue #36, spec §18, §19
#
# Still no database and still no OpenCV: `_advance` reaches the gate through a
# lazy import, so a stub module in `sys.modules` is enough to drive every branch.
# The stub is not merely convenient — it is the same mechanism the API image
# relies on, since it does not install the gate at all.
# ---------------------------------------------------------------------------


class _FakeSession:
    """Enough of an `AsyncSession` for `_advance` to run against."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeCatalogVersions:
    """Enough of `PostgresCardDatabaseVersionRepository` for the record's sake.

    `_advance` asks it for the published catalog identifier and nothing else, so
    a class holding one string is the whole of what has to be stood in for — and
    standing it in keeps these tests free of a database, which is what lets CI
    run them on every push.
    """

    published: str | None = "pokemon-catalog-v0.3.0"

    def __init__(self, _db: Any) -> None:
        pass

    async def current(self) -> Any:
        if self.published is None:
            return None
        return types.SimpleNamespace(version=self.published)


def _run_with_gate(
    monkeypatch: pytest.MonkeyPatch,
    verdict: QualityStatus,
    *,
    recorded: list[dict[str, Any]] | None = None,
    catalog_version: str | None = "pokemon-catalog-v0.3.0",
    snapshot_id: uuid.UUID | None = None,
) -> tuple[list[AnalysisStatus], _FakeEngine]:
    """Drive `_advance` with a stubbed store, a stubbed gate and a stubbed catalog.

    Returns the transitions it attempted, in order, and the engine it built —
    the second so that a test can assert the connection is always released. A
    caller that cares about spec §57's record passes `recorded`, which collects
    the keyword arguments each write was made with; most callers do not.
    """
    engine = _FakeEngine()
    session = _FakeSession()
    moves: list[AnalysisStatus] = []
    writes = recorded if recorded is not None else []

    async def transition(_db: Any, _id: Any, *, to: AnalysisStatus) -> bool:
        moves.append(to)
        return True

    async def prepare_images(_db: Any, _id: Any) -> QualityStatus:
        return verdict

    async def current_snapshot(_db: Any) -> Any:
        if snapshot_id is None:
            return None
        return types.SimpleNamespace(id=snapshot_id)

    async def record_reproducibility(_db: Any, _id: Any, **values: Any) -> None:
        # The moves so far travel with the write, which is what lets a test
        # assert the record is captured *inside* the claim rather than merely
        # at some point during the run.
        writes.append({**values, "after": list(moves)})

    gate = types.ModuleType("tcg_api.analysis.quality")
    gate.prepare_images = prepare_images  # type: ignore[attr-defined]

    versions = type("_Versions", (_FakeCatalogVersions,), {"published": catalog_version})

    monkeypatch.setitem(sys.modules, "tcg_api.analysis.quality", gate)
    monkeypatch.setattr(jobs, "create_engine", lambda: engine)
    monkeypatch.setattr(jobs, "create_session_factory", lambda _engine: lambda: session)
    monkeypatch.setattr(jobs, "transition", transition)
    monkeypatch.setattr(jobs, "record_reproducibility", record_reproducibility)
    monkeypatch.setattr(jobs, "PostgresCardDatabaseVersionRepository", versions)
    monkeypatch.setattr(jobs, "current_snapshot", current_snapshot)

    assert asyncio.run(jobs._advance(uuid.uuid4())) is True
    return moves, engine


@pytest.mark.parametrize(
    "verdict", [QualityStatus.GOOD, QualityStatus.ACCEPTABLE], ids=["good", "acceptable"]
)
def test_photographs_the_gate_is_content_with_reach_the_confirmation_gate(
    monkeypatch: pytest.MonkeyPatch, verdict: QualityStatus
) -> None:
    moves, _ = _run_with_gate(monkeypatch, verdict)

    assert moves == [AnalysisStatus.IDENTIFYING, AnalysisStatus.AWAITING_CONFIRMATION]


def test_poor_photographs_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §19: "If poor, analysis may continue but the user must be informed."

    Continuing is this half; the informing is the findings on `images`, which
    `GET /analyses/{id}` serves and `/analyze` reads before it hands off.
    """
    moves, _ = _run_with_gate(monkeypatch, QualityStatus.POOR)

    assert moves == [AnalysisStatus.IDENTIFYING, AnalysisStatus.AWAITING_CONFIRMATION]


def test_unusable_photographs_stop_the_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §19: "If unusable, analysis should stop."

    Stops meaning `failed`, and — the part worth asserting — **never**
    `awaiting_confirmation`. An analysis that reached the confirmation gate on
    photographs nothing could read would ask the user to confirm a card for an
    analysis that can only ever produce a confident guess.
    """
    moves, _ = _run_with_gate(monkeypatch, QualityStatus.UNUSABLE)

    assert moves == [AnalysisStatus.IDENTIFYING, AnalysisStatus.FAILED]
    assert AnalysisStatus.AWAITING_CONFIRMATION not in moves


def test_the_gate_runs_after_the_claim_rather_than_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate delivery must not decode the photographs a second time.

    Everything after the claim is inside it, so a second delivery finds the row
    already moved and does no work at all — which is what makes an at-least-once
    queue affordable when each job decodes tens of megapixels.
    """
    assessed = []

    async def refuse_to_claim(_db: Any, _id: Any, *, to: AnalysisStatus) -> bool:
        return False

    async def prepare_images(_db: Any, _id: Any) -> QualityStatus:
        assessed.append(True)
        return QualityStatus.GOOD

    gate = types.ModuleType("tcg_api.analysis.quality")
    gate.prepare_images = prepare_images  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "tcg_api.analysis.quality", gate)
    monkeypatch.setattr(jobs, "create_engine", _FakeEngine)
    monkeypatch.setattr(jobs, "create_session_factory", lambda _engine: _FakeSession)
    monkeypatch.setattr(jobs, "transition", refuse_to_claim)

    assert asyncio.run(jobs._advance(uuid.uuid4())) is False
    assert assessed == []


# ---------------------------------------------------------------------------
# Spec §57's reproducibility record — issue #40
# ---------------------------------------------------------------------------


def test_the_reproducibility_record_is_written_inside_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§57's values must be the ones in force when the run began.

    "Inside the claim" is asserted through the moves made so far: the record is
    written after the analysis has been moved to `identifying` and before
    anything else happens, which is the only moment at which the versions in
    force are the versions this analysis was computed against.
    """
    recorded: list[dict[str, Any]] = []
    _run_with_gate(monkeypatch, QualityStatus.GOOD, recorded=recorded)

    assert len(recorded) == 1
    assert recorded[0]["after"] == [AnalysisStatus.IDENTIFYING]
    assert recorded[0]["card_database_version"] == "pokemon-catalog-v0.3.0"
    assert recorded[0]["application_version"] == application_version()


def test_a_refused_photograph_still_carries_its_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An analysis that failed the gate is still an analysis somebody may ask about.

    The record says what the refusal was computed against, so it is written
    before the gate runs rather than on the way to a result the run may never
    reach.
    """
    recorded: list[dict[str, Any]] = []
    moves, _ = _run_with_gate(monkeypatch, QualityStatus.UNUSABLE, recorded=recorded)

    assert moves == [AnalysisStatus.IDENTIFYING, AnalysisStatus.FAILED]
    assert len(recorded) == 1


def test_no_published_catalog_is_recorded_as_none_rather_than_invented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment with no catalog version published records that, honestly.

    The alternative — a placeholder identifier, or the string "current" — is the
    one thing §57 forbids: a record naming a moving target is worse than a
    record naming nothing.
    """
    recorded: list[dict[str, Any]] = []
    _run_with_gate(monkeypatch, QualityStatus.GOOD, recorded=recorded, catalog_version=None)

    assert recorded[0]["card_database_version"] is None


def test_the_market_snapshot_in_force_is_recorded_with_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§36: an analysis is computed against a snapshot resolved when it ran.

    Resolved in the same breath as the catalog version and inside the same
    claim, because "which prices was this computed against" has an answer at
    exactly one moment — and re-resolving it later would answer with whichever
    snapshot happens to be current then.
    """
    snapshot_id = uuid.uuid4()
    recorded: list[dict[str, Any]] = []
    _run_with_gate(monkeypatch, QualityStatus.GOOD, recorded=recorded, snapshot_id=snapshot_id)

    assert recorded[0]["market_snapshot_id"] == snapshot_id
    assert recorded[0]["after"] == [AnalysisStatus.IDENTIFYING]


def test_no_snapshot_is_recorded_as_none_rather_than_invented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing has ingested through V1, and the record says so.

    ADR 0006 gates the provider on a subscription that is not yet active, so no
    snapshot exists — and the honest record of that is `None`, never a
    fabricated identifier and never the prices of some other moment.
    """
    recorded: list[dict[str, Any]] = []
    _run_with_gate(monkeypatch, QualityStatus.GOOD, recorded=recorded)

    assert recorded[0]["market_snapshot_id"] is None


def test_an_unclaimed_delivery_writes_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """A duplicate delivery must not overwrite the record the first run captured.

    The trigger would refuse it, but a job that reaches the write at all has
    already read the catalog and would fail with an `IntegrityError` rather than
    a quiet no-op. Both the read and the write are inside the claim.
    """
    written = []

    async def refuse_to_claim(_db: Any, _id: Any, *, to: AnalysisStatus) -> bool:
        return False

    async def never_resolved(_db: Any) -> Any:
        raise AssertionError("a delivery that did not claim must resolve no snapshot")

    async def record_reproducibility(_db: Any, _id: Any, **_values: Any) -> None:
        written.append(True)

    monkeypatch.setattr(jobs, "create_engine", _FakeEngine)
    monkeypatch.setattr(jobs, "create_session_factory", lambda _engine: _FakeSession)
    monkeypatch.setattr(jobs, "transition", refuse_to_claim)
    monkeypatch.setattr(jobs, "record_reproducibility", record_reproducibility)
    monkeypatch.setattr(jobs, "current_snapshot", never_resolved)

    assert asyncio.run(jobs._advance(uuid.uuid4())) is False
    assert written == []


def test_the_connection_is_released_however_the_gate_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An engine per task run is only affordable if every run disposes of its own."""
    _, engine = _run_with_gate(monkeypatch, QualityStatus.UNUSABLE)

    assert engine.disposed
