"""Logging configuration must be safe to apply more than once.

`create_app()` configures logging, and the test suite builds many apps, so a
non-idempotent configuration would stack handlers and duplicate every line.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tcg_api.app import create_app
from tcg_api.config import Settings
from tcg_api.logging import configure_logging


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_configure_logging_accepts_both_renderers(log_format: str) -> None:
    configure_logging(Settings(log_format=log_format))


def test_configure_logging_does_not_accumulate_handlers() -> None:
    """Every `create_app()` reconfigures logging; stacked handlers would duplicate lines."""
    settings = Settings()

    configure_logging(settings)
    handler_count = len(logging.getLogger().handlers)
    configure_logging(settings)
    configure_logging(settings)

    assert len(logging.getLogger().handlers) == handler_count


def test_configure_logging_applies_the_requested_level() -> None:
    configure_logging(Settings(log_level="DEBUG"))

    assert logging.getLogger().level == logging.DEBUG


def test_uvicorn_loggers_are_routed_through_the_same_pipeline() -> None:
    """Half-structured output is worse than none — uvicorn must not bypass structlog."""
    configure_logging(Settings())

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        assert logger.handlers == []
        assert logger.propagate is True


# ---------------------------------------------------------------------------
# The request line (#266)
# ---------------------------------------------------------------------------
def _json_lines(out: str, event: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in out.splitlines()
        if line.startswith("{") and json.loads(line).get("event") == event
    ]


def _json_app() -> FastAPI:
    return create_app(Settings(_env_file=None, log_format="json"))


def test_a_request_is_logged_with_its_template_and_a_duration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One `api.request_completed` per request: the route *template*, never the path."""
    with TestClient(_json_app()) as client:
        response = client.get("/health")

    (line,) = _json_lines(capsys.readouterr().out, "api.request_completed")
    assert line["method"] == "GET"
    assert line["route"] == "/health"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], float)
    assert line["duration_ms"] >= 0
    assert line["request_id"] == response.headers["X-Request-Id"]


def test_a_client_supplied_request_id_is_ignored() -> None:
    """The id is generated, never accepted — a client cannot forge a join key."""
    with TestClient(_json_app()) as client:
        response = client.get("/health", headers={"X-Request-Id": "attacker"})

    echoed = response.headers["X-Request-Id"]
    assert echoed != "attacker"
    uuid.UUID(echoed)


def test_an_unmatched_path_logs_no_route_and_never_the_raw_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 404 is a request too, and whatever the client typed stays out of the log."""
    with TestClient(_json_app()) as client:
        client.get("/no-such-route-9f3a")

    out = capsys.readouterr().out
    (line,) = _json_lines(out, "api.request_completed")
    assert line["route"] is None
    assert line["status"] == 404
    # The service's own lines only: the test client's `httpx` logger prints
    # the URL it requested, which is not this service's doing.
    service_lines = [line for line in out.splitlines() if '"logger": "tcg_api' in line]
    assert service_lines
    assert not any("no-such-route-9f3a" in line for line in service_lines)


def test_the_request_id_joins_the_error_line_to_the_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The catch-all's line and the request line share the id, and nothing leaks past the request."""
    app = _json_app()

    @app.get("/boom")
    def boom() -> None:
        raise LookupError("boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/boom")

    out = capsys.readouterr().out
    (error,) = _json_lines(out, "api.unhandled_exception")
    (request,) = _json_lines(out, "api.request_completed")
    assert request["status"] == 500
    assert request["request_id"] == error["request_id"]
    assert structlog.contextvars.get_contextvars() == {}
