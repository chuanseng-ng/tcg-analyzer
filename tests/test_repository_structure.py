"""Assert the repository matches the structure mandated by spec §7.

The specification is not checked into this repository, so the expected tree is
transcribed here as a literal. That is deliberate: this file is the in-repo
record of §7, and a change to it should be as visible in review as a change to
the tree itself.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

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
    readme = REPO_ROOT / relative_path / "README.md"
    assert readme.is_file(), f"{relative_path}/ must contain a README.md"
    assert readme.read_text(encoding="utf-8").strip(), f"{relative_path}/README.md is empty"


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


def test_no_model_weights_or_images_are_tracked() -> None:
    """Weights and card images must stay out of history, not merely out of the tree."""
    forbidden = {
        ".pt", ".pth", ".ckpt", ".onnx", ".safetensors", ".h5", ".tflite", ".pkl",
        ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".heic", ".heif", ".bmp",
    }
    tracked = [
        path
        for path in REPO_ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in forbidden
        and ".git" not in path.parts
        and "node_modules" not in path.parts
        and ".venv" not in path.parts
    ]
    assert tracked == []
