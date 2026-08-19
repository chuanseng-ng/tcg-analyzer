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

import pickle
import uuid
from typing import Any

import pytest
from celery.exceptions import Retry
from kombu.exceptions import ContentDisallowed
from kombu.serialization import dumps, loads, prepare_accept_content
from structlog.testing import CapturingLogger
from tcg_api.analysis import jobs
from tcg_api.config import REDIS_URL_ENV_VAR, get_settings

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
