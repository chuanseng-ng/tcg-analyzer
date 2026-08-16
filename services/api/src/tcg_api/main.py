"""ASGI entrypoint: ``uvicorn tcg_api.main:app``."""

from __future__ import annotations

from tcg_api.app import create_app

__all__ = ["app"]

app = create_app()
