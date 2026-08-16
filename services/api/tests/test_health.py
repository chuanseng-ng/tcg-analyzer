"""The health endpoint — spec §8, §57.

`GET /health` is the readiness probe for the API container, so it must stay
dependency-free: no database, no network, no filesystem. It also reports the
`application_version` that spec §57 requires every analysis's reproducibility
record to carry, which is why the version is asserted against package metadata
rather than a literal.
"""

from __future__ import annotations

from importlib.metadata import version

from fastapi.testclient import TestClient

from tcg_api.app import create_app


def test_health_reports_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_a_non_empty_application_version() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.json()["application_version"]


def test_health_application_version_comes_from_package_metadata() -> None:
    """spec §57 — one source of truth, so this must not be a hard-coded literal."""
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.json()["application_version"] == version("tcg-api")


def test_health_response_has_exactly_the_frozen_contract_keys() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert set(response.json()) == {"status", "application_version"}
