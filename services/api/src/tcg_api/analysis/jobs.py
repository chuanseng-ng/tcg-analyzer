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

**What the harness actually does, and what it deliberately does not.** There is
one pipeline stage here, spec §19's image-quality gate, and no ML. A run claims
an analysis whose images have arrived, judges them, and — unless they are
unusable — advances to `awaiting_confirmation`, where it rests. That is not a
stub standing in for a result: spec §20 forbids acting on an identification the
user has not confirmed, no milestone yet produces a candidate, and #104 is the
issue that supplies the confirmation which lets it move on. **Do not make this
reach `completed`** — every result column is still NULL, and a completed
analysis with nothing computed is precisely the confidently-wrong output the
specification forbids.

**Security.** The `python-background-jobs` skill's example configuration is
insecure by omission and none of its snippets are copied here:

1. Serialization is pinned to JSON in three places. A Celery worker that will
   deserialize pickle from a broker an attacker can write to is arbitrary code
   execution, and it is the best-known attack on this stack. Payloads here are a
   single UUID string; JSON is not a compromise.
2. The broker URL comes from `TCG_API_REDIS_URL` and is never defaulted. There
   is no `redis://localhost:6379` fallback, because a fallback is what turns a
   missing setting into an unauthenticated broker nobody notices.
3. The dead-letter record is a log line carrying the job id, the exception type
   and the attempt count — and nothing else. Analysis payloads reference
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
from functools import lru_cache
from uuid import UUID

import structlog
from celery import Celery, Task, shared_task
from celery.exceptions import OperationalError
from celery.utils.time import get_exponential_backoff_interval
from tcg_domain.analysis import AnalysisStatus, QualityStatus

from tcg_api.analysis.state import transition
from tcg_api.config import REDIS_URL_ENV_VAR, get_settings
from tcg_api.database import create_engine, create_session_factory

__all__ = [
    "QUEUE",
    "RUN_ANALYSIS",
    "JobQueueUnavailable",
    "enqueue_analysis",
    "get_celery_app",
    "run_analysis",
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


async def _advance(analysis_id: UUID) -> bool:
    """Run the pipeline for one analysis. Returns whether this call claimed it.

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
            #
            # #40's seam: this is the one place a run begins, and therefore the
            # one place it should resolve and record the card database and
            # grading rules versions it is running against (spec §57).
            claimed = await transition(db, analysis_id, to=AnalysisStatus.IDENTIFYING)
            if not claimed:
                await db.rollback()
                return False

            # Spec §18 puts the quality gate here, before anything looks for a
            # card: refusing a photograph nothing could be read from costs one
            # decode, where letting it through costs the whole pipeline and ends
            # in a confident answer about a blurred rectangle.
            #
            # Imported *here*, not at the top of the module. `routers/analyses.py`
            # imports this file to enqueue, so a module-level import would drag
            # OpenCV into the API image, which does not have it. See
            # `tcg_api.analysis.quality`.
            from tcg_api.analysis.quality import assess_analysis

            verdict = await assess_analysis(db, analysis_id)
            if verdict is QualityStatus.UNUSABLE:
                # §19: "If unusable, analysis should stop." The findings are
                # already written, so the refusal can be explained; the status
                # is the only thing said out loud here (spec §54).
                await transition(db, analysis_id, to=AnalysisStatus.FAILED)
                await db.commit()
                logger.info(
                    "analysis.image_quality_failed",
                    analysis_id=str(analysis_id),
                    quality_status=str(verdict),
                )
                return True

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
    return True


async def _fail(analysis_id: UUID) -> None:
    """Put an analysis into `failed`, from wherever the run left it."""
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as db:
            await transition(db, analysis_id, to=AnalysisStatus.FAILED)
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
)
def run_analysis(self: Task, analysis_id: str) -> None:
    """Advance one analysis as far as this milestone's pipeline goes.

    `analysis_id` is a string because the payload is JSON. It is the
    server-generated identifier `POST /analyses` minted, never anything a client
    chose, so it is safe to key idempotency on.
    """
    identifier = UUID(analysis_id)
    try:
        if not asyncio.run(_advance(identifier)):
            # Not an error. The analysis was already claimed, already past this
            # point, or gone — all of which mean this delivery has nothing to do.
            logger.info("analysis.job_ignored", analysis_id=analysis_id, job_id=self.request.id)
        else:
            logger.info("analysis.job_finished", analysis_id=analysis_id, job_id=self.request.id)
    except Exception as error:
        attempts = (self.request.retries or 0) + 1
        if attempts <= MAX_RETRIES:
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
            logger.warning(
                "analysis.job_retrying",
                analysis_id=analysis_id,
                job_id=self.request.id,
                attempts=attempts,
                countdown=countdown,
            )
            raise self.retry(exc=error, countdown=countdown) from error

        # The dead-letter record: the job, what went wrong, how many times it was
        # tried. Deliberately not the payload, not the traceback and nothing
        # about an image — see rule 3 in the module docstring.
        logger.error(
            "analysis.dead_lettered",
            job_id=self.request.id,
            error=type(error).__name__,
            attempts=attempts,
        )
        _fail_quietly(identifier)
        raise


def _fail_quietly(analysis_id: UUID) -> None:
    """Record the failure on the analysis, without masking the one being raised.

    A store that is itself unreachable is why the run failed as often as not, so
    this must not replace the original exception with a second one — the caller
    is mid-`raise` and the dead-letter line has already been written.
    """
    try:
        asyncio.run(_fail(analysis_id))
    except Exception:  # see the docstring; there is nothing better to do here
        logger.error("analysis.failure_not_recorded", analysis_id=str(analysis_id), exc_info=True)
