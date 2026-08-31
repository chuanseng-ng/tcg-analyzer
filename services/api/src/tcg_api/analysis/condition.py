"""Running the condition step — issue #187, spec §13, §2.7.

The composition itself is `tcg_ml_condition`, which knows nothing about
databases or object storage. This module is the wiring, `quality.py`'s
sibling: read both sides' stored artifacts, derive each side's card frame from
its **stored** `normalization_details` (#182's rule — never the normalizer's
current thresholds), hand bytes and frames to the composer, and write one
document onto the analysis.

**A step that runs always writes a document.** Either the assessment's record
or a top-level ``insufficient_information`` wearing its reason — a missing
artifact, an underivable frame, or the composer's own ``no_axis_measured`` —
each beside the composed version and the four analyzers' threshold records, so
a row explains itself. ``analyses.condition_details`` staying NULL therefore
keeps exactly one meaning: the step never ran.

**Nothing imports this module eagerly, and that is load-bearing** — the same
rule, comment and guard as `quality.py`: `tcg_ml_condition` pulls the four
axis analyzers and with them OpenCV, `routers/analyses.py` imports
`tcg_api.analysis.jobs` merely to enqueue, so `jobs._advance` imports this
file inside the function and `tests/test_import_purity.py` asserts the API
image never reaches it.
"""

from __future__ import annotations

from functools import partial
from typing import Final
from uuid import UUID

import anyio.to_thread
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import V1_SIDES, ImageSide
from tcg_domain.condition import BoundingBox, card_frame_of
from tcg_domain.confidence import InsufficientInformation
from tcg_ml_centering import DEFAULT_CENTERING_THRESHOLDS
from tcg_ml_condition import CONDITION_VERSION
from tcg_ml_condition import assess as assess_artifacts
from tcg_ml_corners import DEFAULT_CORNER_THRESHOLDS
from tcg_ml_edges import DEFAULT_EDGE_THRESHOLDS
from tcg_ml_surface import DEFAULT_SURFACE_THRESHOLDS
from tcg_shared.storage.keys import StorageKey

from tcg_api.analysis.images import read_v1_artifacts
from tcg_api.analysis.sessions import record_condition
from tcg_api.storage import get_object_storage

__all__ = ["CONDITION_VERSION", "assess_condition"]

logger = structlog.get_logger(__name__)

#: The four analyzers' thresholds, merged into the one record stored beside
#: every document. Each ``as_record()`` prefixes its keys with its package
#: name, so the merge cannot collide — the doc-comment promise on each of the
#: four. `assess` runs default thresholds only, which is what makes one
#: module-level record truthful for every call.
_THRESHOLDS_RECORD: Final[dict[str, float]] = {
    **DEFAULT_CENTERING_THRESHOLDS.as_record(),
    **DEFAULT_CORNER_THRESHOLDS.as_record(),
    **DEFAULT_EDGE_THRESHOLDS.as_record(),
    **DEFAULT_SURFACE_THRESHOLDS.as_record(),
}


async def assess_condition(db: AsyncSession, analysis_id: UUID) -> None:
    """Assess `analysis_id`'s condition from its stored artifacts, and record it.

    Does not commit — the caller owns the transaction, so the document lands
    with the transition it precedes or not at all. Never raises over what the
    artifacts *show*: an unassessable analysis is a recorded refusal (spec
    §2.7), not a job failure.

    Raises:
        StorageError: If a stored artifact could not be read. Left to
            propagate, `prepare_images`' rule: the job runner's retry path is
            the right place for an infrastructure failure.
    """
    rows = await read_v1_artifacts(db, analysis_id)

    located: dict[ImageSide, tuple[str, BoundingBox]] = {}
    refusal: str | None = None
    for side in sorted(V1_SIDES):
        row = rows.get(side)
        if row is None or row.normalized_uri is None:
            # No card was located in this photograph (or a caller skipped the
            # pipeline): there is no artifact to measure. The gate's five
            # geometric conditions are `undetermined` for the same reason.
            refusal = f"no_normalized_artifact_for_{side.value}"
            break
        frame = card_frame_of(row.normalization_details)
        if frame is None:
            refusal = f"no_card_frame_for_{side.value}"
            break
        located[side] = (row.normalized_uri, frame)

    if refusal is not None:
        await _record(db, analysis_id, {"insufficient_information": refusal})
        return

    storage = get_object_storage()
    front_uri, front_frame = located[ImageSide.FRONT]
    back_uri, back_frame = located[ImageSide.BACK]
    front = await storage.get(StorageKey(front_uri))
    back = await storage.get(StorageKey(back_uri))

    # Off the event loop, `_prepare_one`'s rule — and this is the heaviest CPU
    # step in the pipeline: four analyzers over two decoded artifacts.
    result = await anyio.to_thread.run_sync(
        partial(
            assess_artifacts,
            front,
            back,
            front_card_frame=front_frame,
            back_card_frame=back_frame,
        )
    )

    if isinstance(result, InsufficientInformation):
        await _record(db, analysis_id, {"insufficient_information": result.reason})
        return
    await _record(db, analysis_id, {"assessment": result.as_record()})


async def _record(db: AsyncSession, analysis_id: UUID, payload: dict[str, object]) -> None:
    """Write the document — version and thresholds first, then the outcome.

    One writer for both outcomes so the two shapes cannot drift, and one log
    event for the same reason (`_log_assessed`'s pattern). The identifier and
    the version, never a measurement describing the photographs (spec §54) —
    the refusal's reason is the one diagnostic worth a line.
    """
    document: dict[str, object] = {
        "version": CONDITION_VERSION,
        "thresholds": _THRESHOLDS_RECORD,
        **payload,
    }
    await record_condition(db, analysis_id, details=document)
    reason = document.get("insufficient_information")
    logger.info(
        "analysis.condition_assessed",
        analysis_id=str(analysis_id),
        version=CONDITION_VERSION,
        refused="assessment" not in document,
        reason=reason,
    )
