"""`application_version` has exactly one source — spec §57.

Every analysis records the application version that produced it, so a version
read from two places could drift and make a historical analysis unreproducible.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import pytest

from tcg_api.version import DISTRIBUTION_NAME, UNKNOWN_VERSION, application_version


def test_application_version_matches_package_metadata() -> None:
    assert application_version() == version(DISTRIBUTION_NAME)


def test_application_version_is_non_empty() -> None:
    assert application_version().strip()


def test_application_version_falls_back_when_the_distribution_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running from a source checkout without an install must not crash the API."""

    def raise_not_found(_: str) -> str:
        raise PackageNotFoundError(DISTRIBUTION_NAME)

    monkeypatch.setattr("tcg_api.version.version", raise_not_found)

    assert application_version() == UNKNOWN_VERSION
