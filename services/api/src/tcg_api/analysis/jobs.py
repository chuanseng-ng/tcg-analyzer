"""The analysis job queue — issue #35, spec §8 and §65.

Spec §8 requires that long-running ML inference never block an HTTP request, so
`POST /analyses/{id}/run` enqueues and answers `queued` while a worker does the
work. This module is both halves of that: the Celery application the API
enqueues through, and the task the worker runs.

**One module, two images.** The worker runs `tcg_api` with a different command,
but from `infrastructure/docker/worker.Dockerfile` rather than the API's image:
#36's quality gate brought OpenCV, and a CV stack has no place in an
internet-facing web server. What that costs this module is one **deliberately
lazy import** — see `_advance` — and `tests/test_import_purity.py` is what keeps
it from being tidied away. Isolation (spec §56) remains the container's: no
published port, no capabilities, `no-new-privileges`.

**What the harness actually does, and what it deliberately does not.** Three
pipeline stages run here: spec §19's image-quality gate, M7's condition step
(#187) and M8's grade prediction step (#227). A run claims an analysis whose
images have arrived, records spec §57's reproducibility values as part of
claiming it, judges the photographs, and — unless they are unusable — assesses
the card's condition, predicts a grade distribution per company from that, and
advances to `awaiting_confirmation`, where it rests. That is not a stub
standing in for a result: spec §20 forbids acting on an identification the
user has not confirmed, no milestone yet produces a candidate, and #104 is the
issue that supplies the confirmation which lets it move on. **The worker never
writes `completed`.** `POST /analyses/{id}/confirm-card` writes `analyzing`
and `POST /analyses/{id}/economic-configuration` writes `completed` (#244),
because the configuration is the last input the results need and #228
composes them on read; at the claim the economics are still unconfigured, and
a completed analysis with nothing to compute from is precisely the
confidently-wrong output the specification forbids.

**Security.** The `python-background-jobs` skill's example configuration is
insecure by omission and none of its snippets are copied here:

1. Serialization is pinned to JSON in three places. A Celery worker that will
   deserialize pickle from a broker an attacker can write to is arbitrary code
   execution, and it is the best-known attack on this stack. Payloads here are a
   single UUID string; JSON is not a compromise.
2. The broker URL comes from `TCG_API_REDIS_URL` and is never defaulted. There
   is no `redis://localhost:6379` fallback, because a fallback is what turns a
   missing setting into an unauthenticated broker nobody notices.
3. The dead-letter record is a log line carrying the job id, the analysis id,
   the exception type, the attempt count and the vocabulary reason the row
   gets (#265) — and nothing else, and never a message. Analysis payloads reference
   photographs of somebody's card, hands and living room (spec §54); a queue
   holding those indefinitely so that a job nobody re-drives could theoretically
   be re-driven is not a trade this project makes.
4. Isolation is the worker container's, as above.
5. The idempotency key is the server-generated `analysis_id`, never anything the
   client supplied — and it is enforced by `state.transition`'s conditional
   `UPDATE` rather than by a key the runner has to remember to check.

**No result backend.** Status is polled through `GET /analyses/{id}`, which
reads PostgreSQL. A second store of the same fact is a second thing that can
disagree with the first.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

import structlog
from celery import Celery, Task, shared_task
from celery.exceptions import OperationalError, SoftTimeLimitExceeded
from celery.utils.time import get_exponential_backoff_interval
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import AnalysisStatus, QualityStatus
from tcg_grading_companies import ADAPTERS
from tcg_grading_companies.errors import GradingCompanyError

from tcg_api.analysis.failures import FailureReason, failure_reason
from tcg_api.analysis.orphans import sweep_orphans
from tcg_api.analysis.retention import (
    SWEEP_INTERVAL_SECONDS,
    SWEEP_LIMIT,
    Swept,
    purge_expired,
)
from tcg_api.analysis.sessions import record_reproducibility
from tcg_api.analysis.stalls import sweep_stalled
from tcg_api.analysis.state import transition
from tcg_api.catalog.versions import PostgresCardDatabaseVersionRepository
from tcg_api.config import REDIS_URL_ENV_VAR, get_settings
from tcg_api.database import create_engine, create_session_factory
from tcg_api.grading.rules import rules_in_force
from tcg_api.market.snapshots import current_snapshot
from tcg_api.storage import get_object_storage
from tcg_api.version import application_version

__all__ = [
    "PURGE_EXPIRED",
    "QUEUE",
    "RUN_ANALYSIS",
    "SWEEP_ORPHANS",
    "SWEEP_STALLED",
    "JobQueueUnavailable",
    "enqueue_analysis",
    "get_celery_app",
    "purge_expired_sessions",
    "run_analysis",
    "sweep_orphan_objects",
    "sweep_stalled_analyses",
]

logger = structlog.get_logger(__name__)

#: The queue analysis work is routed to. Named rather than left as `celery`, so
#: that a second kind of job — the retention sweep in #41, say — can be given a
#: worker of its own without the two competing for prefetch.
QUEUE = "analysis"

#: The task's name on the wire. Written out rather than derived from the module
#: path, because it is a contract between two processes: renaming the module
#: must not silently strand messages already in the queue.
RUN_ANALYSIS = "tcg_api.analysis.run"

#: The retention sweep's name on the wire (#41). Same contract as the above, and
#: the name `celery call` takes to run one by hand.
PURGE_EXPIRED = "tcg_api.analysis.purge_expired"

#: The stall sweep's name on the wire (#272). Same contract again.
SWEEP_STALLED = "tcg_api.analysis.sweep_stalled"

#: The orphan sweep's name on the wire (#264). Same contract again.
SWEEP_ORPHANS = "tcg_api.analysis.sweep_orphans"

#: How many times a failing run is retried before it is dead-lettered. Four
#: attempts in total.
MAX_RETRIES = 3

#: The exponential backoff's base, in seconds: roughly 2, 4, 8, each jittered
#: over the full interval. Jitter rather than a fixed delay because the usual
#: reason several jobs fail at once is that one dependency is down, and
#: retrying them all on the same schedule is how a recovering database gets
#: knocked over again.
RETRY_BACKOFF_SECONDS = 2

#: The ceiling on a single wait, so a long backoff cannot outlive the analysis.
RETRY_BACKOFF_MAX_SECONDS = 60

#: When a run is stopped and recorded as `timed_out` — `docs/observability.md`,
#: "What reads these numbers": six times the analysis-latency budget, past both
#: `/analyze`'s 20 s wait and CI's 30 s ceiling. A run still going here is not
#: slow, it is stuck. Celery raises `SoftTimeLimitExceeded` **inside** the task,
#: so this is the limit that can write a reason; changing it changes that
#: document in the same pull request.
SOFT_TIME_LIMIT_SECONDS = 60

#: The backstop, at twice the soft limit. A soft limit is a signal, and a signal
#: cannot interrupt a C call — an OpenCV kernel that never returns would ignore
#: it. This one kills the child instead, which with `acks_late` and the prefork
#: pool **acks the message**: there is no exception to catch, the run's single
#: transaction rolls back, and the row is left at `uploaded` for the stall sweep
#: below. `/results` stops polling at this number too (#271's `POLL_BUDGET_MS`).
HARD_TIME_LIMIT_SECONDS = 120

#: How many runs one prefork child takes before it is replaced. Every run decodes
#: several megapixels through OpenCV, and a long-lived child's heap only grows —
#: glibc does not return freed arenas to the operating system, so the resident
#: size a container is killed for is the high-water mark of every run it ever did.
#:
#: ponytail: a round number, not a measurement. Lower it if the worker's memory
#: is what a deployment runs out of; nothing here is worth a setting until then.
MAX_TASKS_PER_CHILD = 100

#: Failures a second attempt cannot fix, so the runner does not make one.
#:
#: * `SoftTimeLimitExceeded` — a run that exhausted a minute will exhaust the
#:   next one; four attempts of it is eight minutes of a worker slot.
#: * `GradingCompanyError` — the adapter translates any predictor exception into
#:   one, so a model that raised on this condition document raises on it again.
#: * `ValueError` — a fact about the input rather than about the world. It
#:   covers `InvalidConditionAssessment` (a `ValueError` by declaration) and the
#:   worker's own `UnreadableImage`, which is deliberately **not** imported here:
#:   `tcg_ml_image_quality` is worker-extra only and this module is imported by
#:   the API image (`test_import_purity.py`).
#:
#: Everything else retries: `OSError`, `StorageError`, the driver's
#: `OperationalError` and every store's `ConnectionError` are outages, and an
#: outage is the one thing a backoff is for.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    SoftTimeLimitExceeded,
    GradingCompanyError,
    ValueError,
)


@lru_cache(maxsize=1)
def get_celery_app() -> Celery:
    """Return the process-wide Celery application, built on first use.

    Lazy for the reason `database.get_engine` is lazy: importing this module
    must not require configuration, only using it. That is what lets the API
    import the task to enqueue it while a deployment with no worker still
    starts, and what lets the test suite assert this configuration with no
    broker anywhere.
    """
    settings = get_settings()
    if settings.redis_url is None:
        raise RuntimeError(
            f"{REDIS_URL_ENV_VAR} is not set. Point it at Redis, e.g. "
            f"{REDIS_URL_ENV_VAR}=redis://:password@localhost:6379/0. See .env.example."
        )

    app = Celery("tcg_api", broker=settings.redis_url)
    app.conf.update(
        # Rule 1. All three, because `accept_content` alone still lets this
        # process *send* pickle, and `task_serializer` alone still lets it
        # receive it. Never add "pickle" to any of them.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_default_queue=QUEUE,
        # Acknowledge after the work, not before, so a worker killed mid-run
        # leaves the job on the queue. That makes delivery at-least-once, which
        # is safe here only because `state.transition` makes a second delivery a
        # no-op — the two settings are one decision.
        task_acks_late=True,
        # With late acks, a worker holding a batch it has not started is a batch
        # nobody else can run. One at a time.
        worker_prefetch_multiplier=1,
        # A worker that starts before Redis is up should wait rather than exit;
        # Compose orders them, but a restarting broker should not need Compose.
        broker_connection_retry_on_startup=True,
        # #272. Configuration rather than a command-line flag for the reason the
        # beat schedule below is: a deployment that starts a worker gets it.
        worker_max_tasks_per_child=MAX_TASKS_PER_CHILD,
        # A publish that cannot reach the broker must fail the HTTP request
        # quickly rather than retry inside it. Celery's default policy retries
        # three times with a growing interval, which is right for a worker and
        # wrong for a caller holding a socket open.
        task_publish_retry_policy={
            "max_retries": 2,
            "interval_start": 0,
            "interval_step": 0.2,
            "interval_max": 0.5,
        },
        # Celery replaces the root logger's handlers on worker startup unless
        # told not to, which would throw away the structlog pipeline
        # `worker.py` configured — and leave the dead-letter record as a dict
        # repr inside a plain log message rather than a line anything can parse.
        worker_hijack_root_logger=False,
        timezone="UTC",
        enable_utc=True,
        # Spec §54's retention sweep (#41). Beat is embedded in the worker with
        # `--beat` rather than given a service of its own — see the Compose
        # file. The schedule is here rather than there so that a deployment
        # cannot forget it, and `options` names the queue explicitly even though
        # `task_default_queue` would already route it: the sweep and the
        # analysis run share a worker today and need not always.
        beat_schedule={
            "purge-expired-sessions": {
                "task": PURGE_EXPIRED,
                "schedule": SWEEP_INTERVAL_SECONDS,
                "options": {"queue": QUEUE},
            },
            # #272's stall sweep, on the retention sweep's schedule because it
            # answers the same kind of question — what has been sitting here too
            # long — and because an analysis nobody is waiting on any more is
            # not worth a scheduler of its own.
            "sweep-stalled-analyses": {
                "task": SWEEP_STALLED,
                "schedule": SWEEP_INTERVAL_SECONDS,
                "options": {"queue": QUEUE},
            },
            # #264's orphan sweep, on the same schedule as the sweep whose blind
            # spot it covers. It reads the day prefixes the retention sweep has
            # already finished with, so running the two an arbitrary distance
            # apart would change nothing.
            "sweep-orphan-objects": {
                "task": SWEEP_ORPHANS,
                "schedule": SWEEP_INTERVAL_SECONDS,
                "options": {"queue": QUEUE},
            },
        },
    )
    return app


class JobQueueUnavailable(ConnectionError):
    """The job queue could not be reached, or was never configured.

    The broker's counterpart of `sessions.AnalysisStoreUnavailable`, and an
    ordinary `ConnectionError` for the same reason: the HTTP layer answers 503
    without having to know that the queue happens to be Celery over Redis.
    """


def enqueue_analysis(analysis_id: UUID) -> str:
    """Hand one analysis to a worker, returning the job's identifier.

    `send_task` by name rather than `run_analysis.delay`, so the application
    doing the sending is the one this module built rather than whichever Celery
    instance happens to be current — a distinction that costs nothing here and
    stops being invisible the moment a second application exists in a test.

    The task itself is never imported by the caller. All the API needs to know
    about a job is its name and its one argument.
    """
    try:
        result = get_celery_app().send_task(RUN_ANALYSIS, args=[str(analysis_id)], queue=QUEUE)
    except (OperationalError, OSError, RuntimeError) as error:
        raise JobQueueUnavailable("The job queue could not be reached.") from error
    return str(result.id)


@contextmanager
def _step(name: str) -> Iterator[None]:
    """Time one pipeline step and say so — spec §67's latencies, one line each.

    A step that raised is not completed and gets no line: the retry or the
    dead-letter line is its record.
    """
    started = time.perf_counter()
    yield
    logger.info(
        "analysis.step_completed",
        step=name,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )


async def _advance(analysis_id: UUID) -> AnalysisStatus | None:
    """Run the pipeline for one analysis.

    Returns the state the run left the analysis in, or `None` when this call
    did not claim it.

    The engine is built and disposed inside this coroutine rather than taken
    from `database.get_engine`, because an asyncpg pool belongs to the event
    loop that created it and every task run is a fresh `asyncio.run`. Reusing
    the cached one across loops is the classic way this fails: it works for the
    first task and then hangs.
    `# ponytail: an engine per task run; give the worker one persistent loop if
    the task rate ever makes the connection setup measurable.`
    """
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as db:
            # The claim. Also the idempotency check and the concurrency guard —
            # see `state.transition`. Everything below is inside the claim, so a
            # second delivery reaches none of it.
            claimed = await transition(db, analysis_id, to=AnalysisStatus.IDENTIFYING)
            if not claimed:
                await db.rollback()
                return None

            # Imported *here*, not at the top of the module: `tcg_ml_condition`
            # pulls the four axis analyzers and with them OpenCV, and
            # `routers/analyses.py` imports this file to enqueue, so a
            # module-level import would drag OpenCV into the API image, which
            # does not have it. See `tcg_api.analysis.quality` and
            # `tcg_api.analysis.condition`. The grading wiring binds no OpenCV
            # but matches the `tcg_ml_` prefix `test_import_purity.py` probes,
            # so it is deferred all the same. After the claim, so an unclaimed
            # delivery imports nothing; before the timer below, so `versions`
            # measures the reads and not the first run's cold import.
            from tcg_api.analysis.condition import CONDITION_VERSION, assess_condition
            from tcg_api.analysis.grading import GRADING_VERSION, predict_grades
            from tcg_api.analysis.quality import prepare_images

            # Spec §57, immediately after the claim and inside it. This is the
            # one place a run begins, so it is the one moment at which "which
            # versions is this analysis being computed against" has an answer —
            # resolved to explicit values and written once. Only the run that
            # won the claim reaches this, so the record has exactly one writer,
            # and `trg_analyses_reproducibility_immutable` refuses a second.
            #
            # `current()` yields the identifier of a published catalog, never a
            # pointer to whichever is current later; None means none had been
            # published, which is a fact rather than a gap. An unreachable
            # catalog raises `CatalogUnavailable` and is left to propagate: the
            # store is down, so the run should fail, roll the claim back and be
            # retried.
            with _step("versions"):
                current_catalog = await PostgresCardDatabaseVersionRepository(db).current()
                # §36: the analysis is computed against a snapshot resolved
                # now, not against whichever is current when somebody re-reads
                # the result. `None` through V1 and honestly so — ADR 0006
                # gates the provider on a subscription that is not yet active,
                # so nothing has ingested and there is no snapshot. Never a
                # fabricated one.
                snapshot = await current_snapshot(db)
                # §57's rules version, resolved against the table like the
                # catalog version above, and before the gate for the same
                # reason the model bundle is: the record says what was in
                # force, not which stages completed. See
                # `_grading_rules_version`.
                rules_version = await _grading_rules_version(db)

                await record_reproducibility(
                    db,
                    analysis_id,
                    # The *worker's* version, which is the process producing
                    # the result — not the API's, and not the one that opened
                    # the session days ago.
                    application_version=application_version(),
                    card_database_version=(
                        None if current_catalog is None else current_catalog.version
                    ),
                    market_snapshot_id=None if snapshot is None else snapshot.id,
                    # Compile-time constants of the ml packages, so resolvable
                    # at the claim like every other §57 field — and recorded
                    # whether or not the run reaches either step, because the
                    # record says which versions were in force, not which
                    # stages completed (#187). The grading version composes in
                    # after the condition version, ADR 0011 decision 6.
                    model_bundle_version=f"{CONDITION_VERSION}+{GRADING_VERSION}",
                    grading_rules_version=rules_version,
                )

            # Spec §18 puts the quality gate here, before anything looks for a
            # card: refusing a photograph nothing could be read from costs one
            # decode, where letting it through costs the whole pipeline and ends
            # in a confident answer about a blurred rectangle. Both sides in one
            # step; `image.assessed` carries each side's own duration.
            with _step("gate"):
                verdict = await prepare_images(db, analysis_id)
            if verdict is QualityStatus.UNUSABLE:
                # §19: "If unusable, analysis should stop." The findings are
                # already written, so the refusal can be explained; the status
                # and its reason are the only things said out loud here (spec
                # §54). The reason rides in the same statement as the move
                # (#265) — the one failure that is the user's to fix.
                await transition(
                    db,
                    analysis_id,
                    to=AnalysisStatus.FAILED,
                    failure=FailureReason.UNUSABLE_PHOTOGRAPH,
                )
                await db.commit()
                logger.info(
                    "analysis.image_quality_failed",
                    analysis_id=str(analysis_id),
                    quality_status=str(verdict),
                )
                return AnalysisStatus.FAILED

            # The condition step — M7's acceptance criterion (#187): both sides'
            # artifacts in, one recorded assessment out, uncertainty included. It
            # runs for every analysis the gate lets through (`poor` continues,
            # per §19) and inside the claim, so the document lands in the same
            # transaction as the transition below. It never consults the card's
            # identity — the representation is neutral by the master
            # architectural rule — which is why it can run before confirmation.
            with _step("condition"):
                await assess_condition(db, analysis_id)

            # The grade prediction step — M8's acceptance criterion (#227): the
            # document the step above stored in, a distribution per company
            # out, refusals included. Same claim, same transaction, and before
            # confirmation for the same reason: it reads the neutral
            # representation and never the card's identity (ADR 0011).
            with _step("grading"):
                await predict_grades(db, analysis_id)

            # Where identification would run. It does not exist in any decomposed
            # milestone yet, so the analysis reaches the confirmation gate with no
            # candidate and the user names the card themselves (#91, #104).
            #
            # A `poor` verdict continues, per §19 — and is not silent: the
            # findings are on the images and `GET /analyses/{id}` serves them, so
            # `/analyze` can tell the user before they go on.
            await transition(db, analysis_id, to=AnalysisStatus.AWAITING_CONFIRMATION)
            await db.commit()
    finally:
        await engine.dispose()
    return AnalysisStatus.AWAITING_CONFIRMATION


async def _grading_rules_version(db: AsyncSession) -> str | None:
    """Spec §57's `grading_rules_version`: the standards in force at the claim.

    One string for three companies — each company's `grading_rules.version`
    as `rules_in_force` resolves it against the **table** (never the package,
    `GET /grading-companies`' rule), joined with `+` in slug order. All three,
    because at the claim no company has been selected: the economic
    configuration naming them arrives later, and all three standards were in
    force (ADR 0011). A V1 predictor reads no machine-readable rules; the
    record says what was in force, not what was consulted.

    `None` when any company has no standard recorded as in force — a partial
    composite would read as complete and misreport. The log names the slug.
    The date is this process's, `routers/grading.py`'s reasoning: reference
    data that changes every few years is not a clock-skew question. A store
    that cannot be read raises `GradingRulesUnavailable` and is left to
    propagate, `CatalogUnavailable`'s rule: fail the run, retry.
    """
    today = datetime.now(UTC).date()
    versions: list[str] = []
    # ponytail: one statement per company against a three-row table, the
    # router's own deferral. One windowed statement if the list ever grows.
    for company in sorted(ADAPTERS):
        rules = await rules_in_force(db, company, today)
        if rules is None:
            logger.warning("analysis.grading_rules_not_in_force", company=company)
            return None
        versions.append(rules.version)
    return "+".join(versions)


async def _fail(analysis_id: UUID, reason: FailureReason) -> None:
    """Put an analysis into `failed`, from wherever the run left it, saying why.

    "Wherever" is usually `uploaded`: a run that raised never committed its
    claim, so the row is back where the run found it and every non-terminal
    state may move to `failed`. The reason is vocabulary chosen from the
    exception's type (#265), never its text.
    """
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as db:
            await transition(db, analysis_id, to=AnalysisStatus.FAILED, failure=reason)
            await db.commit()
    finally:
        await engine.dispose()


# `shared_task` rather than `@app.task`, because `@app.task` needs an
# application and building one needs a broker URL — which would make *importing*
# this module require configuration, and the API imports it merely to enqueue.
# A shared task registers itself onto every application that is finalized after
# it is defined, which is exactly the lazy binding wanted here.
@shared_task(
    bind=True,
    name=RUN_ANALYSIS,
    max_retries=MAX_RETRIES,
    acks_late=True,
    # #272. On the task rather than in the configuration, so the two sweeps
    # below — hourly, re-runnable, and bounded by the work that is due rather
    # than by one photograph — do not inherit a limit meant for a decode.
    soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    time_limit=HARD_TIME_LIMIT_SECONDS,
)
def run_analysis(self: Task, analysis_id: str) -> None:
    """Advance one analysis as far as this milestone's pipeline goes.

    `analysis_id` is a string because the payload is JSON. It is the
    server-generated identifier `POST /analyses` minted, never anything a client
    chose, so it is safe to key idempotency on.
    """
    identifier = UUID(analysis_id)
    started = time.perf_counter()
    # Bound for the whole run, so every line a step logs carries them without
    # naming them itself; `asyncio.run` copies this context into the loop.
    # Cleared in the `finally` below — a prefork child runs many tasks in a
    # row, and a bind that outlived its run would stamp the next one's lines.
    structlog.contextvars.bind_contextvars(analysis_id=analysis_id, job_id=self.request.id)
    try:
        outcome = asyncio.run(_advance(identifier))
        if outcome is None:
            # Not an error. The analysis was already claimed, already past this
            # point, or gone — all of which mean this delivery has nothing to do.
            logger.info("analysis.job_ignored", analysis_id=analysis_id, job_id=self.request.id)
        else:
            # `total_ms` is the task's, entry to return: one event loop and
            # one engine ahead of the claim. Spec §67's analysis latency is
            # claim → `awaiting_confirmation`, the sum of the step lines.
            logger.info(
                "analysis.job_finished",
                analysis_id=analysis_id,
                job_id=self.request.id,
                outcome=str(outcome),
                total_ms=round((time.perf_counter() - started) * 1000, 1),
            )
    except Exception as error:
        attempts = (self.request.retries or 0) + 1
        # A permanent failure skips the backoff and lands on the record below on
        # its first attempt — `attempts` is what tells the two apart in the log.
        # `SoftTimeLimitExceeded` arrives here like any other exception, which
        # is what lets the soft limit write a reason at all (#272).
        if attempts <= MAX_RETRIES and not isinstance(error, PERMANENT_ERRORS):
            # The countdown is computed rather than left to `retry_backoff`:
            # that setting is only consulted by the wrapper `autoretry_for`
            # installs, and this task catches its own exceptions so it can write
            # the dead-letter line below. Same helper Celery's own wrapper uses.
            countdown = get_exponential_backoff_interval(
                factor=RETRY_BACKOFF_SECONDS,
                retries=self.request.retries or 0,
                maximum=RETRY_BACKOFF_MAX_SECONDS,
                full_jitter=True,
            )
            # The type and never the message, as on the dead-letter line: a
            # run that retries and then succeeds would otherwise leave no
            # record of what went wrong (spec §67's provider errors).
            logger.warning(
                "analysis.job_retrying",
                analysis_id=analysis_id,
                job_id=self.request.id,
                error=type(error).__name__,
                attempts=attempts,
                countdown=countdown,
            )
            raise self.retry(exc=error, countdown=countdown) from error

        # The dead-letter record: the job, what went wrong, how many times it was
        # tried. Deliberately not the payload, not the traceback and nothing
        # about an image — see rule 3 in the module docstring. The reason is
        # the same closed vocabulary the row gets (#265), chosen from the
        # exception's type and never its message.
        reason = failure_reason(error)
        logger.error(
            "analysis.dead_lettered",
            analysis_id=analysis_id,
            job_id=self.request.id,
            error=type(error).__name__,
            reason=reason.value,
            attempts=attempts,
        )
        _fail_quietly(identifier, reason)
        raise
    finally:
        structlog.contextvars.clear_contextvars()


def _fail_quietly(analysis_id: UUID, reason: FailureReason) -> None:
    """Record the failure on the analysis, without masking the one being raised.

    A store that is itself unreachable is why the run failed as often as not, so
    this must not replace the original exception with a second one — the caller
    is mid-`raise` and the dead-letter line has already been written.
    """
    try:
        asyncio.run(_fail(analysis_id, reason))
    except Exception:  # see the docstring; there is nothing better to do here
        logger.error("analysis.failure_not_recorded", analysis_id=str(analysis_id), exc_info=True)


async def _purge(limit: int) -> Swept:
    """Run one sweep. Engine built and disposed here, for `_advance`'s reason."""
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as db:
            return await purge_expired(db, get_object_storage(), limit=limit)
    finally:
        await engine.dispose()


async def _sweep_stalled(limit: int) -> int:
    """Run one stall sweep. Engine built and disposed here, as `_purge` does."""
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as db:
            return await sweep_stalled(db, limit=limit)
    finally:
        await engine.dispose()


async def _sweep_orphans(limit: int) -> int:
    """Run one orphan sweep. Engine built and disposed here, as `_purge` does."""
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as db:
            return await sweep_orphans(
                db,
                get_object_storage(),
                ttl_seconds=get_settings().session_ttl_seconds,
                limit=limit,
            )
    finally:
        await engine.dispose()


# No retries. A tick that cannot reach PostgreSQL or the object store is far
# more likely to be an outage than a fluke, and the next tick is an hour away —
# which is a gentler retry than any backoff, and leaves the rows due until it
# succeeds. `acks_late` still applies, so a worker killed mid-sweep leaves the
# message on the queue; the sweep is re-runnable by construction.
@shared_task(name=PURGE_EXPIRED, max_retries=0, acks_late=True)
def purge_expired_sessions() -> None:
    """Delete everything belonging to sessions past their expiry — spec §54.

    Scheduled hourly by the beat embedded in the worker. It takes no arguments:
    what is due is a fact about the database's clock, not something a caller
    gets to assert.
    """
    asyncio.run(_purge(SWEEP_LIMIT))


# No retries and no time limit, for `purge_expired_sessions`' reasons: the next
# tick is a gentler retry than any backoff, and a sweep killed halfway leaves
# rows that are still stalled an hour later.
@shared_task(name=SWEEP_STALLED, max_retries=0, acks_late=True)
def sweep_stalled_analyses() -> None:
    """Fail every analysis whose run was killed without saying so — #272.

    The backstop's backstop. A hard time limit kills the prefork child, and
    Celery acks the message on the way out: no exception reaches
    `run_analysis`, so nothing writes a failure and the row is left exactly
    where the rolled-back transaction put it. This is what reaches those rows,
    and takes no arguments for the reason the retention sweep does not.
    """
    asyncio.run(_sweep_stalled(SWEEP_LIMIT))


# No retries and no time limit, for the two sweeps above: the next tick is a
# gentler retry than any backoff, and this one leaves nothing half-done — it
# writes no row at all.
@shared_task(name=SWEEP_ORPHANS, max_retries=0, acks_late=True)
def sweep_orphan_objects() -> None:
    """Delete expired objects that no row names — #264, spec §54.

    The retention sweep's blind spot. It works from rows, and an object whose
    row was never committed has no row to be found from; this one works from the
    day prefix in the key instead. It takes no arguments for the reason neither
    of the others does: what has expired is a fact about the database's clock
    and the configured period, not something a caller gets to assert.
    """
    asyncio.run(_sweep_orphans(SWEEP_LIMIT))
