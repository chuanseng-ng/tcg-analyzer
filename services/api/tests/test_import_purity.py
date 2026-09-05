"""The request path must reach neither the catalog source nor the CV stack.

Two boundaries, one mechanism.

ADR 0004: "no import-time dependency anywhere the request path can reach". The
import pipeline is an adapter run from a console script; the FastAPI app serves
what that adapter already wrote. Nothing about serving a card should require an
HTTP client, and an accidental re-export in `tcg_api.catalog.__init__` is all it
would take to acquire one — at which point the boundary is decorative.

#36 added the second, #37 and #38 widened it to three packages, and #187 to
eight: the image-quality gate lives in `ml/image-quality`, the card detector
in `ml/card-detection`, perspective correction in `ml/normalization`, and the
condition step in `ml/condition` — which pulls the four axis analyzers
(`ml/centering`, `ml/corners`, `ml/edges`, `ml/surface`) transitively. All of
them bring OpenCV, `infrastructure/docker/worker.Dockerfile` is the only image
that installs any, and `tcg_api.analysis.jobs` therefore imports their wiring
*inside* `_advance` rather than at module scope — the API imports `jobs` merely
to enqueue. Moving those imports to the top of the file looks like tidying and
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


def test_the_condition_wiring_is_the_module_that_binds_the_condition_packages() -> None:
    """Guard the guard for #187's step, on the quality wiring's terms.

    `tcg_api.analysis.condition` is the second module `_advance` imports
    lazily, and the registration point the four axis analyzers deferred to
    their first importer: it must actually reach all five condition packages
    (and through them OpenCV), or the CI assertions about the worker image
    would be asserting an installation nothing exercises.
    """
    pulled = _modules_matching("cv2", after_importing="tcg_api.analysis.condition")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.analysis.condition")

    assert "cv2" in pulled
    assert {
        "tcg_ml_centering",
        "tcg_ml_condition",
        "tcg_ml_corners",
        "tcg_ml_edges",
        "tcg_ml_surface",
    } <= set(stages), stages


def test_the_grading_wiring_is_the_module_that_binds_the_grading_packages() -> None:
    """Guard the guard for #227's step — the first of these with no OpenCV.

    `tcg_api.analysis.grading` is the third module `_advance` imports lazily,
    and the registration point the three predictors deferred to their first
    importer (ADR 0011 decision 5). They bind no CV stack, so `cv2` is not
    asserted; what makes the lazy import load-bearing for them is the
    `tcg_ml_` prefix probe above, which they match all the same.
    """
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.analysis.grading")

    assert {
        "tcg_ml_grading_bgs",
        "tcg_ml_grading_psa",
        "tcg_ml_grading_tag",
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
        "is tcg_api.datasets.normalization's, which is a module that may."
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
    produced out of band by the normalization pass, which is why OpenCV is not
    on the ingest path.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.ingestion")

    assert cv == [], cv


def test_splitting_a_corpus_pulls_in_neither_opencv_nor_the_analysis_stages() -> None:
    """#156's splitter is a plain command over the database, and must stay one.

    It reads stored grouping keys and stored hashes. Importing
    `tcg_api.datasets.deduplication` — which produces the artifact those hashes
    are taken over — would bind it to OpenCV for a step it never runs, and make a
    pure function over grouping keys a worker-image command.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.splitting")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.splitting")

    assert cv == [], (
        f"importing tcg_api.datasets.splitting pulled in {cv}. The splitter consumes "
        "tcg_api.datasets.fingerprints, which is Pillow and arithmetic; producing an "
        "artifact is tcg_api.datasets.normalization's, and the splitter must not reach it."
    )
    assert stages == [], stages


def test_versioning_a_dataset_pulls_in_neither_opencv_nor_the_analysis_stages() -> None:
    """#157 publishes a version from the API image, not the worker one.

    It reaches the corpus through the splitter, which reaches stored hashes
    through `tcg_api.datasets.fingerprints`. Freezing a corpus decodes no
    photograph, so a CV stack in this path would be a dependency for a step it
    never runs — and `tcg-publish-dataset-version` would need the worker image to
    write a row.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.versioning")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.versioning")

    assert cv == [], (
        f"importing tcg_api.datasets.versioning pulled in {cv}. Publishing a version "
        "reads rows and writes rows; it never produces a normalized artifact, which is "
        "tcg_api.datasets.normalization's work and the only reason OpenCV is installed."
    )
    assert stages == [], stages


def test_registering_a_model_bundle_pulls_in_neither_opencv_nor_the_analysis_stages() -> None:
    """#189 registers a bundle from the API image, not the worker one.

    The registry stores a reference — a name, a version and an object-storage
    key — and never opens the artifact it names. A CV stack on this path would
    make `tcg-register-model-bundle` a worker-image command for a step that
    decodes no photograph, which is the claim the pyproject comment and
    `docs/database.md` both make and this test holds.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.models.registration")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.models.registration")

    assert cv == [], (
        f"importing tcg_api.models.registration pulled in {cv}. Registering a bundle "
        "writes one row; it never reads the artifact's bytes, let alone decodes them."
    )
    assert stages == [], stages


def test_recording_a_grading_outcome_pulls_in_neither_opencv_nor_the_analysis_stages() -> None:
    """#165 records an outcome from the API image, not the worker one.

    It writes what an operator read off a slab. No photograph is opened, let
    alone decoded, so a CV stack on this path would make
    `tcg-record-grading-outcome` a worker-image command for a step that has no
    bytes — the claim the pyproject comment makes and this test holds.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.outcomes")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.outcomes")

    assert cv == [], (
        f"importing tcg_api.datasets.outcomes pulled in {cv}. Recording an outcome "
        "writes two rows; the photographs it labels are never opened."
    )
    assert stages == [], stages


def test_the_normalization_pass_is_the_module_that_binds_to_opencv() -> None:
    """Guard the guard for #159's pass, on the deduplication test's terms.

    `tcg_api.datasets.normalization` owns the one detect-then-straighten path, so
    it is a module that *may* reach OpenCV — and deleting the seam would
    otherwise 'fix' every assertion above by making the stack unreachable
    everywhere.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.normalization")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.normalization")

    assert "cv2" in cv
    assert {"tcg_ml_card_detection", "tcg_ml_normalization"} <= set(stages), stages


def test_the_evaluation_runner_is_the_module_that_binds_the_benchmark() -> None:
    """Guard the guard for #188's runner, on the normalization test's terms.

    `tcg_api.datasets.evaluation` runs the four analyzers over a corpus, so it
    is a module that *may* reach OpenCV — and it must actually reach
    `tcg_ml_evaluation` too, or the worker-image CI assertion about that
    package would be asserting an installation nothing exercises.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.evaluation")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.evaluation")

    assert "cv2" in cv
    assert {
        "tcg_ml_centering",
        "tcg_ml_condition",
        "tcg_ml_corners",
        "tcg_ml_edges",
        "tcg_ml_evaluation",
        "tcg_ml_surface",
    } <= set(stages), stages


def test_the_grade_runner_binds_the_analyzers_and_all_three_predictors() -> None:
    """Guard the guard for #242's runner, on the condition runner's terms.

    `tcg_api.datasets.grade_evaluation` reaches a `ConditionAssessment` the
    way the condition runner does — the four analyzers, so OpenCV — and then
    hands it to the three predictors, so all three `tcg_ml_grading_*` packages
    must actually load or the worker-image CI assertion about them would be
    asserting an installation this command never exercises.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.grade_evaluation")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.grade_evaluation")

    assert "cv2" in cv
    assert {
        "tcg_ml_centering",
        "tcg_ml_condition",
        "tcg_ml_corners",
        "tcg_ml_edges",
        "tcg_ml_evaluation",
        "tcg_ml_grading_bgs",
        "tcg_ml_grading_psa",
        "tcg_ml_grading_tag",
        "tcg_ml_surface",
    } <= set(stages), stages


def test_serving_a_training_image_pulls_in_neither_opencv_nor_the_analysis_stages() -> None:
    """#159's read layer serves stored columns and a stored object, and nothing else.

    The artifact reaches an annotator because a pass produced it out of band, not
    because a request straightened a photograph while somebody waited. That is not
    a performance preference: `tcg_api.main` may not reach the CV stack at all, so
    normalizing on demand is unavailable rather than merely slow — which is the
    whole reason `training_images.normalized_uri` is a column.
    """
    cv = _modules_matching("cv2", after_importing="tcg_api.datasets.annotation")
    stages = _modules_matching("tcg_ml_", after_importing="tcg_api.datasets.annotation")

    assert cv == [], (
        f"importing tcg_api.datasets.annotation pulled in {cv}. The annotation reads "
        "resolve rows and fetch a stored object; producing an artifact is "
        "tcg_api.datasets.normalization's, and the request path must not reach it."
    )
    assert stages == [], stages
