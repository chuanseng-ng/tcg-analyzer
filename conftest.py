"""Shared pytest configuration for the Python workspace.

`Settings` reads a `.env` file so a developer can configure the stack once and
forget about it. That is right for running the service and wrong for running the
suite: from the moment `.env.example` exists, most developers have a `.env`, and
a test asserting a default would then be asserting whatever that developer
happens to have set. The suite would pass here and fail there.

So the file is switched off for the duration of every test. Real environment
variables still apply — integration tests are selected by an exported
`TCG_API_DATABASE_URL` (see CLAUDE.md), and that keeps working; only the
*file* is ignored.
"""

from __future__ import annotations

import pytest
from tcg_api.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_from_the_developers_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Describe the code, not the machine it happens to be running on."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
