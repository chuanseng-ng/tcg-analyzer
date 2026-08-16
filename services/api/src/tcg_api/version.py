"""The single source of truth for the application version.

Spec §57 requires every analysis to record the `application_version` that
produced it, alongside the model bundle, card database, grading rules, market
snapshot and economic configuration versions. A historical analysis must remain
reproducible, which is only possible if the version it recorded is the version
that actually ran.

The version therefore lives in exactly one place — the ``tcg-api`` distribution
metadata, populated from ``services/api/pyproject.toml`` at build time. Nothing
else in the codebase may hard-code a version string; read it from here.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["DISTRIBUTION_NAME", "UNKNOWN_VERSION", "application_version"]

#: The installed distribution whose metadata carries the version.
DISTRIBUTION_NAME = "tcg-api"

#: Reported when the distribution is not installed — a source checkout run
#: without ``uv sync``. It is deliberately a valid, obviously-not-a-release
#: version so a reproducibility record can never silently claim a real one.
UNKNOWN_VERSION = "0.0.0+unknown"


def application_version() -> str:
    """Return the running application version for the spec §57 record.

    Falls back to :data:`UNKNOWN_VERSION` rather than raising: the health
    endpoint doubles as a container readiness probe and must answer even from a
    degraded environment.
    """
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION
