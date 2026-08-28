"""The request path must reach neither the catalog source nor the CV stack.

Two boundaries, one mechanism.

ADR 0004: "no import-time dependency anywhere the request path can reach". The
import pipeline is an adapter run from a console script; the FastAPI app serves
what that adapter already wrote. Nothing about serving a card should require an
HTTP client, and an accidental re-export in `tcg_api.catalog.__init__` is all it
would take to acquire one — at which point the boundary is decorative.

#36 added the second, and #37 and #38 widened it to three packages. The
image-quality gate lives in `ml/image-quality`, the card detector in
`ml/card-detection` and perspective correction in `ml/normalization`; all three
bring OpenCV, `infrastructure/docker/worker.Dockerfile` is the only image
that installs either, and `tcg_api.analysis.jobs` therefore imports their wiring
*inside* `_advance` rather than at module scope — the API imports `jobs` merely
to enqueue. Moving that import to the top of the file looks like tidying and
produces an API container that cannot start, so it is asserted here rather than
left to a comment.

The same shape as `packages/shared/tests/test_storage_purity.py`, and for the
same reason: the only honest place to check what an import drags in is a real
import in a fresh interpreter.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

PROBE = textwrap.dedent(
    """
    import json
    import sys

    import {module}  # noqa: F401
    print(json.dumps(sorted(name for name in sys.modules if name.startswith("{prefix}"))))
    """
)


def _modules_matching(prefix: str, *, after_importing: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(module=after_importing, prefix=prefix)],
        capture_output=True,
        text=True,
        check=True,
    )
    # `tcg_api.main` configures logging and announces startup on import, so the
    # probe's answer is the last line rather than the whole of stdout.
    found: list[str] = json.loads(result.stdout.strip().splitlines()[-1])
    return found


def test_serving_the_api_pulls_in_neither_the_source_adapter_nor_an_http_client() -> None:
    pulled = _modules_matching("httpx", after_importing="tcg_api.main")
    adapter = _modules_matching("tcg_api.catalog.tcgdex", after_importing="tcg_api.main")

    assert pulled == [], (
        f"importing tcg_api.main pulled in {pulled}. The request path serves what "
        "the import pipeline already wrote; it must never acquire a client for "
        "the catalog source (ADR 0004)."
    )
    assert adapter == []


def test_the_tcgdex_adapter_is_the_module_that_binds_to_the_http_client() -> None:
    """Guard the guard: without this, deleting the adapter would 'fix' the test above."""
    pulled = _modules_matching("httpx", after_importing="tcg_api.catalog.tcgdex")

    assert "httpx" in pulled


def test_serving_the_api_pulls_in_neither_opencv_nor_the_analysis_stages() -> None:
    """The whole reason there are two images (#36, #37, #38).

    `api.Dockerfile` installs none of `tcg-ml-image-quality`,
    `tcg-ml-card-detection` or `tcg-ml-normalization`, so this is not merely
    about image size: a module-level import on the request path is an
    `ImportError` at startup in the deployed API container, and it would be
    found in a deployment rather than here.

    `analysis/images.py` is the near miss worth naming. It is imported by
    `routers/analyses.py`, and `record_normalization` writes what
    `ml/normalization` produced — so a signature naming that package's
    `Normalized` would land here rather than in review.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.main")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.main")

    assert cv == [], (
        f"importing tcg_api.main pulled in {cv}. The gate, the detector and "
        "perspective correction run in the worker image; the API image does not "
        "install OpenCV and would fail to start. Keep the import inside "
        "`jobs._advance`."
    )
    assert stages == [], stages


def test_enqueueing_a_job_does_not_pull_in_the_analysis_stages() -> None:
    """Guard the guard, one layer down.

    `routers/analyses.py` imports `tcg_api.analysis.jobs` to enqueue, so that
    module is the one place the lazy import has to hold. Asserting it directly
    means the failure names the module that broke it rather than the whole app.
    """
    pulled = _modules_matching("cv2", after_importing="tcg_api.analysis.jobs")

    assert pulled == [], pulled


def test_the_quality_wiring_is_the_module_that_binds_to_opencv() -> None:
    """Guard the guard: without this, deleting the gate would 'fix' the tests above."""
    pulled = _modules_matching("cv2", after_importing="tcg_api.analysis.quality")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.analysis.quality")

    assert "cv2" in pulled
    assert {
        "tcg_ml_card_detection",
        "tcg_ml_image_quality",
        "tcg_ml_normalization",
    } <= set(stages), stages


def test_deriving_duplicate_groups_pulls_in_neither_opencv_nor_the_analysis_stages() -> None:
    """#155's pure half stays runnable outside the worker image, and #156 needs it to.

    The splitter is a plain command over the database: it reads stored hashes and
    groups them, and runs no image processing at all. If the grouping lived beside
    the warp it would acquire OpenCV for a step it never executes — the same
    failure the two tests above exist for, one milestone later.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.fingerprints")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.fingerprints")

    assert cv == [], (
        f"importing tcg_api.datasets.fingerprints pulled in {cv}. The hash, the "
        "distance and the grouping are Pillow and arithmetic; producing an artifact "
        "is tcg_api.datasets.deduplication's, which is the module that may."
    )
    assert stages == [], stages


def test_the_deduplication_pass_is_the_module_that_binds_to_opencv() -> None:
    """Guard the guard: without this, deleting the seam would 'fix' the test above."""
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.deduplication")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.deduplication")

    assert "cv2" in cv
    assert {"tcg_ml_card_detection", "tcg_ml_normalization"} <= set(stages), stages


def test_ingesting_a_training_image_pulls_in_neither_opencv_nor_the_analysis_stages() -> None:
    """#154's own claim, which nothing asserted until #155 gave it a sibling.

    Ingestion validates with Pillow and stores the bytes; the artifact is
    produced on demand by the deduplication pass, which is why OpenCV is not on
    the ingest path.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.ingestion")

    assert cv == [], cv
