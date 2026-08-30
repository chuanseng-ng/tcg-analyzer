"""The domain package must import nothing but the standard library.

`packages/domain` is imported by the API, the analysis service and every ML
module. A framework, database driver or provider client acquired here would be
acquired by all of them, and the domain would stop being portable. This test
guards that at the only place it can be checked honestly: a real import in a
fresh interpreter.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

# Snapshotting `sys.modules` around the import ignores whatever the interpreter
# and the editable install already loaded, so the assertion is about tcg_domain
# alone.
PROBE = textwrap.dedent(
    """
    import json
    import sys

    before = set(sys.modules)
    import tcg_domain  # noqa: F401
    imported = {name.split(".")[0] for name in set(sys.modules) - before}
    allowed = set(sys.stdlib_module_names) | {"tcg_domain"}
    print(json.dumps(sorted(imported - allowed)))
    """
)


def test_importing_the_domain_pulls_in_no_third_party_module() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    third_party = json.loads(result.stdout)
    assert third_party == [], (
        f"tcg_domain imported non-stdlib modules: {third_party}. "
        "The domain package must stay free of framework, database and provider "
        "dependencies."
    )


def test_the_package_declares_no_runtime_dependencies() -> None:
    import tomllib
    from pathlib import Path

    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == []
    assert "optional-dependencies" not in project


def test_the_public_surface_is_explicit() -> None:
    import tcg_domain

    exported = {name for name in vars(tcg_domain) if not name.startswith("_")}
    # Submodules are reachable as attributes once imported, and `annotations` is
    # the __future__ flag every module here imports. Neither is public surface,
    # so neither is listed in __all__. Note the two spellings a line apart:
    # `annotation` is spec §30's vocabulary module, `annotations` is the flag,
    # and the module is singular precisely so importing it cannot shadow one with
    # the other.
    incidental = {
        "analysis",
        "annotation",
        "annotations",
        "card",
        "card_geometry",
        "catalog",
        "catalog_version",
        "condition",
        "confidence",
        "dataset",
        "distribution",
        "errors",
        "grade",
        "identification",
        "image_quality",
        "money",
        "repository",
    }
    assert exported - incidental == set(tcg_domain.__all__)
