"""The port must not depend on a provider; the adapter is where boto3 lives.

CLAUDE.md's "external providers are replaceable" invariant is easy to state and
easy to erode — one convenience re-export in ``__init__`` and every importer of
the port has acquired boto3, at which point the port is decorative. The only
honest place to check it is a real import in a fresh interpreter, which is what
:mod:`packages.domain.tests.test_domain_purity` does for the domain and what
this does for storage.

The second test is the one that keeps the first meaningful: it asserts the
adapter *does* reach boto3, so the pair cannot both pass by the storage package
having quietly lost its S3 support.
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

    before = set(sys.modules)
    import {module}  # noqa: F401
    imported = {{name.split(".")[0] for name in set(sys.modules) - before}}
    allowed = set(sys.stdlib_module_names) | {{"tcg_shared"}}
    print(json.dumps(sorted(imported - allowed)))
    """
)


def _third_party_modules_pulled_in_by(module: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(module=module)],
        capture_output=True,
        text=True,
        check=True,
    )
    imported: list[str] = json.loads(result.stdout)
    return imported


def test_importing_the_port_pulls_in_no_provider() -> None:
    third_party = _third_party_modules_pulled_in_by("tcg_shared.storage")

    assert third_party == [], (
        f"tcg_shared.storage imported non-stdlib modules: {third_party}. "
        "The port, its errors, its keys and the in-memory adapter must stay free "
        "of any provider client — bind to one explicitly via tcg_shared.storage.s3."
    )


def test_the_s3_adapter_is_the_module_that_binds_to_boto3() -> None:
    """Guard the guard: without this, deleting the adapter would 'fix' the test above."""
    third_party = _third_party_modules_pulled_in_by("tcg_shared.storage.s3")

    assert "boto3" in third_party


SURFACE_PROBE = textwrap.dedent(
    """
    import json

    import tcg_shared.storage as storage

    print(json.dumps({
        "exported": sorted(n for n in vars(storage) if not n.startswith("_")),
        "declared": sorted(storage.__all__),
    }))
    """
)


def test_the_public_surface_is_explicit() -> None:
    """Read in a fresh interpreter, because a submodule imported anywhere in the
    suite becomes an attribute of the package and would otherwise make this
    assertion depend on test ordering."""
    result = subprocess.run(
        [sys.executable, "-c", SURFACE_PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    surface = json.loads(result.stdout)

    # Submodules become attributes once imported, and `annotations` is the
    # __future__ flag every module here carries. Neither is public surface.
    incidental = {"annotations", "errors", "keys", "memory", "port"}

    assert set(surface["exported"]) - incidental == set(surface["declared"])
