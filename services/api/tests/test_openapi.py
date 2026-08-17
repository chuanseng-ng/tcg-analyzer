"""The OpenAPI schema is the sole source of frontend types — ADR 0001.

`apps/web` generates its TypeScript types from this schema rather than importing
a shared package, so an endpoint that is served but undocumented, or documented
without a response model, is a broken contract rather than a cosmetic omission.
"""

from __future__ import annotations

from importlib.metadata import version

from fastapi.testclient import TestClient
from tcg_api.app import create_app


def test_openapi_json_is_served() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200


def test_openapi_generation_does_not_raise() -> None:
    assert create_app().openapi()


def test_openapi_documents_the_health_path() -> None:
    schema = create_app().openapi()

    assert "/health" in schema["paths"]
    assert "get" in schema["paths"]["/health"]


def test_openapi_documents_the_health_response_schema() -> None:
    """A bare dict response would leave apps/web without a generated type."""
    schema = create_app().openapi()

    content = schema["paths"]["/health"]["get"]["responses"]["200"]["content"]
    reference = content["application/json"]["schema"]["$ref"]
    model_name = reference.rsplit("/", 1)[-1]

    properties = schema["components"]["schemas"][model_name]["properties"]
    assert set(properties) == {"status", "application_version"}


def test_openapi_reports_the_application_version() -> None:
    schema = create_app().openapi()

    assert schema["info"]["version"] == version("tcg-api")
