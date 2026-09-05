"""Assert the repository matches the structure mandated by spec §7.

The specification is not checked into this repository, so the expected tree is
transcribed here as a literal. That is deliberate: this file is the in-repo
record of §7, and a change to it should be as visible in review as a change to
the tree itself.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# spec §7 — every directory the specification names.
# --------------------------------------------------------------------------
SPEC_DIRECTORIES: tuple[str, ...] = (
    "apps",
    "apps/web",
    "apps/annotation",
    "services",
    "services/api",
    "services/analysis",
    "services/market-data",
    "services/ingestion",
    "ml",
    "ml/card-detection",
    "ml/card-identification",
    "ml/image-quality",
    "ml/centering",
    "ml/corners",
    "ml/edges",
    "ml/surface",
    "ml/condition",
    "ml/grading",
    "ml/grading/psa",
    "ml/grading/tag",
    "ml/grading/bgs",
    "ml/evaluation",
    "packages",
    "packages/domain",
    "packages/grading-companies",
    "packages/market-data",
    "packages/economic-engine",
    "packages/shared",
    "database",
    "database/migrations",
    "database/seeds",
    "database/fixtures",
    "datasets",
    "datasets/schemas",
    "datasets/manifests",
    "datasets/documentation",
    "infrastructure",
    "infrastructure/docker",
    "infrastructure/local",
    "infrastructure/deployment",
    "docs",
    "tests",
    ".github",
    ".github/workflows",
)

# The filename a directory's own documentation takes, where it is not README.md.
DIRECTORY_DOC_FILENAME: dict[str, str] = {
    # GitHub surfaces .github/README.md as the repository's front page, ahead of
    # the root one. Same document, under a name GitHub does not claim.
    ".github": "CONTENTS.md",
}

# --------------------------------------------------------------------------
# Python workspace members: directory -> (distribution name, import name).
#
# `packages/*` are Python so that services/api and every ml/ module can import
# them — see docs/adr/0001-language-boundaries-in-the-monorepo.md.
#
# `ml/grading/` is a grouping directory and deliberately absent: it is not a
# package.
# --------------------------------------------------------------------------
PYTHON_PACKAGES: dict[str, tuple[str, str]] = {
    "packages/domain": ("tcg-domain", "tcg_domain"),
    "packages/grading-companies": ("tcg-grading-companies", "tcg_grading_companies"),
    "packages/market-data": ("tcg-market-data", "tcg_market_data"),
    "packages/economic-engine": ("tcg-economic-engine", "tcg_economic_engine"),
    "packages/shared": ("tcg-shared", "tcg_shared"),
    "services/api": ("tcg-api", "tcg_api"),
    "services/analysis": ("tcg-analysis", "tcg_analysis"),
    # Distinct from packages/market-data, which shares a directory name.
    "services/market-data": ("tcg-market-data-service", "tcg_market_data_service"),
    "services/ingestion": ("tcg-ingestion", "tcg_ingestion"),
    "ml/card-detection": ("tcg-ml-card-detection", "tcg_ml_card_detection"),
    "ml/card-identification": ("tcg-ml-card-identification", "tcg_ml_card_identification"),
    "ml/image-quality": ("tcg-ml-image-quality", "tcg_ml_image_quality"),
    # Not one of spec §7's names. Spec §18 makes perspective correction and
    # normalization a stage of its own, and #38 gave it a package of its own on
    # the reasoning the two siblings above are shaped by — one stage, one
    # package, no sibling dependency.
    "ml/normalization": ("tcg-ml-normalization", "tcg_ml_normalization"),
    "ml/centering": ("tcg-ml-centering", "tcg_ml_centering"),
    "ml/corners": ("tcg-ml-corners", "tcg_ml_corners"),
    "ml/edges": ("tcg-ml-edges", "tcg_ml_edges"),
    "ml/surface": ("tcg-ml-surface", "tcg_ml_surface"),
    "ml/condition": ("tcg-ml-condition", "tcg_ml_condition"),
    "ml/grading/psa": ("tcg-ml-grading-psa", "tcg_ml_grading_psa"),
    "ml/grading/tag": ("tcg-ml-grading-tag", "tcg_ml_grading_tag"),
    "ml/grading/bgs": ("tcg-ml-grading-bgs", "tcg_ml_grading_bgs"),
    "ml/evaluation": ("tcg-ml-evaluation", "tcg_ml_evaluation"),
}


@pytest.mark.parametrize("relative_path", SPEC_DIRECTORIES)
def test_spec_directory_exists(relative_path: str) -> None:
    directory = REPO_ROOT / relative_path
    assert directory.is_dir(), f"spec §7 requires the directory {relative_path}/"


@pytest.mark.parametrize("relative_path", SPEC_DIRECTORIES)
def test_spec_directory_documents_its_responsibility(relative_path: str) -> None:
    filename = DIRECTORY_DOC_FILENAME.get(relative_path, "README.md")
    doc = REPO_ROOT / relative_path / filename
    assert doc.is_file(), f"{relative_path}/ must contain a {filename}"
    assert doc.read_text(encoding="utf-8").strip(), f"{relative_path}/{filename} is empty"


@pytest.mark.parametrize(
    ("relative_path", "distribution", "import_name"),
    [(path, dist, imp) for path, (dist, imp) in PYTHON_PACKAGES.items()],
)
def test_python_package_manifest(relative_path: str, distribution: str, import_name: str) -> None:
    manifest = REPO_ROOT / relative_path / "pyproject.toml"
    assert manifest.is_file(), f"{relative_path}/ must contain a pyproject.toml"

    project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
    assert project["name"] == distribution
    assert (REPO_ROOT / relative_path / "src" / import_name).is_dir(), (
        f"{relative_path}/ must use a src layout containing {import_name}/"
    )


def test_python_distribution_names_are_unique() -> None:
    names = [dist for dist, _ in PYTHON_PACKAGES.values()]
    assert len(names) == len(set(names))


def test_ml_grading_is_a_grouping_directory_not_a_package() -> None:
    assert not (REPO_ROOT / "ml" / "grading" / "pyproject.toml").exists()


def test_workspace_roots_exist() -> None:
    for manifest in ("pyproject.toml", "package.json", "pnpm-workspace.yaml"):
        assert (REPO_ROOT / manifest).is_file(), f"missing workspace manifest {manifest}"


def test_uv_workspace_covers_every_python_package() -> None:
    root = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    members = root["tool"]["uv"]["workspace"]["members"]
    excluded = root["tool"]["uv"]["workspace"].get("exclude", [])

    covered: set[str] = set()
    for pattern in members:
        for match in REPO_ROOT.glob(pattern):
            relative = match.relative_to(REPO_ROOT).as_posix()
            if match.is_dir() and relative not in excluded:
                covered.add(relative)

    assert covered == set(PYTHON_PACKAGES)


FORBIDDEN_SUFFIXES: frozenset[str] = frozenset(
    {
        # Model weights and serialised artifacts.
        ".pt",
        ".pth",
        ".ckpt",
        ".onnx",
        ".safetensors",
        ".h5",
        ".tflite",
        ".pkl",
        # Card photography.
        ".jpg",
        ".jpeg",
        ".webp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
        ".bmp",
    }
)

#: The one place an image suffix is tracked on purpose: the browser journey's
#: fixture photographs, rendered by `services/api/tests/test_anonymous_journey.py`'s
#: generators. They are photographs of nothing — no card, no provenance, no
#: training value — and `.gitignore` re-includes exactly this directory.
SYNTHETIC_FIXTURES = "apps/web/e2e/fixtures/"


def _tracked_files() -> list[str]:
    """Every path in git's index, as repository-relative POSIX strings.

    Asking git rather than walking the filesystem is both the faster and the
    more truthful answer. Walking meant descending into `node_modules` — 97% of
    the paths under the repository root once `apps/web` has dependencies — only
    to discard them, and it meant reporting on files git has never been told
    about, which is not what "tracked" means. An exclusion list is also
    unnecessary here: `node_modules`, `.venv` and `.git` are untracked by
    definition, so they cannot appear.
    """
    # Resolved rather than relying on PATH lookup at exec time: it satisfies
    # the "no partial executable path" lint, and it turns a missing git into a
    # sentence instead of a bare FileNotFoundError from deep inside subprocess.
    git = shutil.which("git")
    assert git is not None, "git is required to check which files are tracked"

    completed = subprocess.run(
        [git, "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return [path for path in completed.stdout.split("\0") if path]


def test_no_model_weights_or_images_are_tracked() -> None:
    """Weights and card images must never enter the repository.

    The project may be open-sourced later, so these must stay out of history
    rather than merely out of the working tree. This checks the index, which is
    the last point at which that is still cheap to enforce; nothing here can
    speak to commits that were already made.
    """
    offenders = [
        path
        for path in _tracked_files()
        if PurePosixPath(path).suffix.lower() in FORBIDDEN_SUFFIXES
        and not path.startswith(SYNTHETIC_FIXTURES)
    ]
    assert offenders == []


def test_the_tracked_file_listing_is_not_silently_empty() -> None:
    """Guard the guard: an empty listing would make the check above vacuous."""
    tracked = _tracked_files()
    assert len(tracked) > 50
    assert "pyproject.toml" in tracked
