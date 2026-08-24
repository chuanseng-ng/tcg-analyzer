"""The port must not depend on a provider; #52's adapter is where a client lives.

This is #49's acceptance criterion — "nothing in the core depends on a concrete
provider" — executed rather than asserted in prose. It is easy to state and easy
to erode: one convenience re-export in `__init__` and every importer of the port
has acquired an HTTP client, at which point the port is decorative and ADR
0006's whole "the vendor enters behind §33's interface and nothing more" is a
comment.

The only honest place to check it is a real import in a fresh interpreter, which
is what `packages/domain/tests/test_domain_purity.py` does for the domain and
`packages/shared/tests/test_storage_purity.py` for storage.

There is no "guard the guard" twin asserting that some module *does* reach a
provider, because there is no adapter yet. #52 adds one, so that this file
cannot pass by the package having quietly lost its provider support.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

# The workspace packages this one is allowed to reach, and no others. Both are
# themselves stdlib-only, so the transitive claim holds.
ALLOWED_WORKSPACE = ("tcg_market_data", "tcg_domain", "tcg_grading_companies")

PROBE = textwrap.dedent(
    """
    import json
    import sys

    before = set(sys.modules)
    import tcg_market_data  # noqa: F401
    imported = {{name.split(".")[0] for name in set(sys.modules) - before}}
    allowed = set(sys.stdlib_module_names) | {allowed!r}
    print(json.dumps(sorted(imported - allowed)))
    """
).format(allowed=set(ALLOWED_WORKSPACE))


def test_importing_the_port_pulls_in_no_provider() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    third_party = json.loads(result.stdout)

    assert third_party == [], (
        f"tcg_market_data imported non-stdlib modules: {third_party}. "
        "The port, its errors and the in-memory provider must stay free of any "
        "provider client — bind to one explicitly from its own module."
    )


def test_the_package_declares_only_workspace_dependencies() -> None:
    """A third-party dependency here would be one every importer inherits."""
    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]

    assert project["dependencies"] == ["tcg-domain", "tcg-grading-companies"]
    # Not an extra either: CI resolves with `uv sync --all-packages`, which does
    # not install extras, so an extra would be a dependency that is absent in CI
    # and present locally.
    assert "optional-dependencies" not in project


def test_the_public_surface_is_explicit() -> None:
    import tcg_market_data

    exported = {name for name in vars(tcg_market_data) if not name.startswith("_")}
    # Submodules become attributes once imported, and `annotations` is the
    # __future__ flag every module carries. Neither is public surface.
    incidental = {"annotations", "errors", "memory", "port"}

    assert exported - incidental == set(tcg_market_data.__all__)
