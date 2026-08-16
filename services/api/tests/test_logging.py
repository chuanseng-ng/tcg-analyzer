"""Logging configuration must be safe to apply more than once.

`create_app()` configures logging, and the test suite builds many apps, so a
non-idempotent configuration would stack handlers and duplicate every line.
"""

from __future__ import annotations

import logging

import pytest

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
