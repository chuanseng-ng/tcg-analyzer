"""Running the image pipeline's first two stages — issues #36, #37, spec §18, §19.

The stages themselves are `tcg_ml_card_detection` and `tcg_ml_image_quality`,
neither of which knows anything about databases or object storage. This module
is the wiring: read what was stored, locate the card, judge the photograph
against spec §19, write the verdict back, and answer with what it means for the
analysis.

**Detection runs before the gate, which is not the order spec §18 draws.** §18
lists image quality above card detection, and that is still the order the
*refusal* happens in — the gate is the stage that stops an analysis, and the
detector never stops anything. But five of spec §19's eleven conditions are
about where the card is, so the boundary has to be found before the gate can
answer them. What §18 fixes is which stage owns the verdict, and that is
unchanged.

**Nothing imports this module eagerly, and that is load-bearing.**
`routers/analyses.py` imports `tcg_api.analysis.jobs` merely to enqueue, so a
module-level import of this file anywhere on that path would pull OpenCV into
the internet-facing API image — which is precisely what
`infrastructure/docker/worker.Dockerfile` exists to prevent. `jobs._advance`
imports it inside the function, and `tests/test_import_purity.py` asserts that
importing the application does not reach `cv2`. Do not "tidy" that import to the
top of `jobs.py`; the symptom is not a slow import, it is an API container that
will not start.
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

import anyio.to_thread
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import QualityStatus
from tcg_domain.image_quality import QualityReport, worst_status
from tcg_ml_card_detection import detect
from tcg_ml_image_quality import assess
from tcg_shared.storage.keys import StorageKey
from tcg_shared.storage.port import ObjectStorage

from tcg_api.analysis.images import read_v1_image_keys, record_quality
from tcg_api.storage import get_object_storage

__all__ = ["assess_analysis"]

logger = structlog.get_logger(__name__)


async def assess_analysis(db: AsyncSession, analysis_id: UUID) -> QualityStatus:
    """Judge every photograph of `analysis_id` and record what the gate found.

    Returns the analysis's verdict: the worst of its images', because spec §19's
    rules are about whether the *analysis* may proceed and one unusable
    photograph is enough to stop it.

    Does not commit — the caller owns the transaction, so the verdicts and the
    transition they cause land together.

    Raises:
        StorageError: If a stored image could not be read. Left to propagate:
            the job runner's retry and dead-letter path is the right place for
            an infrastructure failure, and a gate that swallowed one would pass
            photographs it had never seen.
        UnreadableImage: If a stored image does not decode, which means the
            object is not what its row says it is. Same treatment.
    """
    keys = await read_v1_image_keys(db, analysis_id)

    statuses: list[QualityStatus] = []
    for side in sorted(keys):
        # Built inside the loop, and deliberately not hoisted above it.
        # `get_object_storage` raises when the store is unconfigured, so
        # hoisting turns "this analysis has no photographs" — which folds to
        # `good` and needs no store at all — into a job failure about
        # configuration. It is `lru_cache`d, so the second side is free.
        report = await _assess_one(get_object_storage(), keys[side])
        await record_quality(db, analysis_id=analysis_id, side=side, report=report)
        statuses.append(report.status)
        # The verdict and the score, never the URI and never a measurement that
        # could describe the photograph itself (spec §54).
        logger.info(
            "image.assessed",
            analysis_id=str(analysis_id),
            side=str(side),
            quality_status=str(report.status),
            quality_score=round(report.score, 3),
            gate=report.version,
            # Whether the card was located at all, which is the difference
            # between five answered conditions and five undetermined ones — and
            # therefore the first thing to look at when a photograph nobody can
            # fault comes back `acceptable`.
            detector=report.detector,
        )

    return worst_status(statuses)


async def _assess_one(storage: ObjectStorage, key: str) -> QualityReport:
    """Fetch one stored photograph and judge it, off the event loop.

    Decoding tens of megapixels, walking its contours and convolving a Laplacian
    over the result is CPU-bound, and a blocking call on the loop is an outage
    under load rather than a slow request — the same reason the upload endpoint
    puts `validate_image` behind `run_in_threadpool`. One hop for both stages,
    not one each.
    """
    data = await storage.get(StorageKey(key))
    return await anyio.to_thread.run_sync(partial(_locate_and_judge, data))


def _locate_and_judge(data: bytes) -> QualityReport:
    """Where the card is, and what spec §19 makes of the photograph around it.

    A detector that cannot find a card returns
    :class:`~tcg_domain.confidence.InsufficientInformation` rather than raising,
    and the gate reports the five geometric conditions undetermined with its
    reason. Undecodable bytes are the one case both stages meet: `detect`
    answers rather than raising, so `assess` stays the single place that turns
    them into an `UnreadableImage` for the job runner.

    ponytail: two decodes of the same JPEG, tens of milliseconds inside a
    background job. Pass a decoded array between the two if a profile ever says
    it matters — the cost is that both packages then take a numpy array instead
    of bytes, which is a worse contract for a worse reason.

    #38 needs this quadrilateral for perspective correction and it exists right
    here; returning it alongside the report is that issue's change, not a
    second detection pass.
    """
    return assess(data, geometry=detect(data))
