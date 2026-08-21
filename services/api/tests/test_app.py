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


def test_retry_after_is_readable_by_the_browser() -> None:
    """`Retry-After` is useless to `apps/web` unless CORS exposes it.

    A cross-origin response hands script only CORS's six safelisted headers
    unless the server names more — and `allow_headers` is about the *request*,
    so it does not help. Without this the 429 the limiter raises (ADR 0005)
    arrives with no readable wait, and the only thing #34's upload screen could
    then offer is a button that fires straight back into the limit.

    Found by driving a real browser; `curl` reads every header regardless, which
    is why no earlier test noticed.
    """
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    exposed = response.headers["access-control-expose-headers"]

    assert "Retry-After" in {header.strip() for header in exposed.split(",")}
