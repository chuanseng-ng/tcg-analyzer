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

That leaves one gap, which `unconfigured_environment` below closes. A test
asserting what the service does with a setting **absent** cannot construct
`Settings(_env_file=None)` and stop there: `_env_file` suppresses the file and
nothing else, so on a developer's machine — or in any shell that followed
CLAUDE.md's instruction to export `TCG_API_DATABASE_URL` before running the
integration tests — the setting is present after all and the test asserts the
opposite of what it says. Ask for the fixture instead.
"""

from __future__ import annotations

import os

import pytest
from tcg_api.config import Settings, get_settings
from tcg_api.storage import get_object_storage

#: The prefix every setting this service reads is spelled with.
_ENV_PREFIX = "TCG_API_"

# Every process-wide cache derived from settings. Clearing settings alone would
# leave a client built from the previous test's environment in place, which is
# the same leak one step further down.
_CACHED_ON_SETTINGS = (get_settings, get_object_storage)


@pytest.fixture(autouse=True)
def _isolate_from_the_developers_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Describe the code, not the machine it happens to be running on."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for cached in _CACHED_ON_SETTINGS:
        cached.cache_clear()
    yield
    for cached in _CACHED_ON_SETTINGS:
        cached.cache_clear()


@pytest.fixture
def unconfigured_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every `TCG_API_` variable for the duration of one test.

    Opt-in rather than autouse, and deliberately so: the suite selects its
    integration and object-storage tests by exported variables, and clearing
    those everywhere would skip the tests that need them. Only a test asserting
    *unconfigured* behaviour wants this.

    The caches are cleared as well as the variables, because a `Settings` object
    built earlier in the session would otherwise still hold the value that was
    just removed.
    """
    for name in [name for name in os.environ if name.startswith(_ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)
    for cached in _CACHED_ON_SETTINGS:
        cached.cache_clear()
