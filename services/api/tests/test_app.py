"""Application assembly — the frozen contract other M0 branches build on.

`create_app()` and `tcg_api.main:app` are depended on by #14 (the web shell) and
#15 (persistence), so their names and shapes are fixed.
"""

from __future__ import annotations

from importlib.metadata import version

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tcg_api.app import create_app


def test_create_app_returns_a_fastapi_application() -> None:
    assert isinstance(create_app(), FastAPI)


def test_create_app_returns_a_fresh_application_each_call() -> None:
    assert create_app() is not create_app()


def test_application_version_is_the_openapi_version() -> None:
    assert create_app().version == version("tcg-api")


def test_main_exposes_a_module_level_app_for_uvicorn() -> None:
    """`uvicorn tcg_api.main:app` is the documented and Dockerfile entrypoint."""
    from tcg_api import main

    assert isinstance(main.app, FastAPI)


def test_cross_origin_requests_from_the_web_shell_are_permitted() -> None:
    """#14's Next.js dev server on :3000 must reach this API on :8000."""
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
