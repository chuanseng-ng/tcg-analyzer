"""Running the image-quality gate over an analysis — issue #36, spec §18, §19.

The gate itself is `tcg_ml_image_quality`, which knows nothing about databases
or object storage. This module is the wiring: read what was stored, judge it,
write the verdict back, and answer with what it means for the analysis.

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
    storage = get_object_storage()
    keys = await read_v1_image_keys(db, analysis_id)

    statuses: list[QualityStatus] = []
    for side in sorted(keys):
        report = await _assess_one(storage, keys[side])
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
        )

    return worst_status(statuses)


async def _assess_one(storage: ObjectStorage, key: str) -> QualityReport:
    """Fetch one stored photograph and judge it, off the event loop.

    Decoding tens of megapixels and convolving a Laplacian over the result is
    CPU-bound, and a blocking call on the loop is an outage under load rather
    than a slow request — the same reason the upload endpoint puts
    `validate_image` behind `run_in_threadpool`.
    """
    data = await storage.get(StorageKey(key))
    return await anyio.to_thread.run_sync(partial(assess, data))
