"""The economics must not be able to reach the grading models, or anything else.

CLAUDE.md calls "grading is separate from economics" the master architectural
rule and requires it to survive every future version. Prose does not survive
anything; a real import in a fresh interpreter does. This package answers "is
grading worth it?" from numbers it is handed, so the honest check is that
importing it pulls in nothing but the standard library and `tcg_domain` — no
`tcg_ml_*`, no provider client, no SQLAlchemy.

It is written at #58, when the package holds one module, because that is the
only moment the list is short enough to be obviously right. #59 to #64 each add a
formula, and each one is a chance to reach for something.

Modelled on `packages/domain/tests/test_domain_purity.py` and
`packages/market-data/tests/test_market_data_purity.py`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

# The one workspace package the economics may reach. It is stdlib-only itself,
# so the transitive claim holds.
ALLOWED_WORKSPACE = ("tcg_economic_engine", "tcg_domain")

PROBE = textwrap.dedent(
    """
    import json
    import sys

    before = set(sys.modules)
    import tcg_economic_engine  # noqa: F401
    imported = {{name.split(".")[0] for name in set(sys.modules) - before}}
    allowed = set(sys.stdlib_module_names) | {allowed!r}
    print(json.dumps(sorted(imported - allowed)))
    """
).format(allowed=set(ALLOWED_WORKSPACE))


def test_importing_the_engine_pulls_in_nothing_else() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    third_party = json.loads(result.stdout)

    assert third_party == [], (
        f"tcg_economic_engine imported non-stdlib modules: {third_party}. "
        "The economic engine takes prices and a distribution as inputs; it must "
        "never depend on how either was produced."
    )


def test_the_package_declares_only_the_domain() -> None:
    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]

    assert project["dependencies"] == ["tcg-domain"]
    # Not an extra either: CI resolves with `uv sync --all-packages`, which does
    # not install extras, so an extra would be absent in CI and present locally.
    assert "optional-dependencies" not in project


def test_the_public_surface_is_explicit() -> None:
    import tcg_economic_engine

    exported = {name for name in vars(tcg_economic_engine) if not name.startswith("_")}
    # Submodules become attributes once imported, and `annotations` is the
    # __future__ flag every module carries. Neither is public surface.
    incidental = {"annotations", "costs", "errors", "expectation", "profit"}

    assert exported - incidental == set(tcg_economic_engine.__all__)
