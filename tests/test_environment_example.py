"""`.env.example` is the contract between the settings code and a new developer.

It only works if it is complete. A setting that exists in code but not in the
example is a variable nobody knows to set; a key in the example that no setting
reads is a variable that silently does nothing. Both drift in silence, so both
are asserted here rather than checked by hand at review time.

The file is also the reason no credential needs to be committed (spec §77): it
carries placeholders, and the real values live in an untracked `.env`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tcg_api.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_EXAMPLE = REPO_ROOT / ".env.example"
WEB_EXAMPLE = REPO_ROOT / "apps" / "web" / ".env.example"

ASSIGNMENT = re.compile(r"^\s*(?P<key>[A-Z][A-Z0-9_]*)\s*=(?P<value>.*)$")


def keys_of(path: Path) -> dict[str, str]:
    """Every `KEY=value` assignment in an env file, comments and blanks ignored."""
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.match(line)
        if match:
            entries[match["key"]] = match["value"].strip()
    return entries


def settings_variables() -> set[str]:
    """The environment variable name of every field on `Settings`.

    Read from the model rather than transcribed, so a new setting cannot be
    added without this test noticing.
    """
    prefix = Settings.model_config["env_prefix"]
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        names.add(alias if isinstance(alias, str) else f"{prefix}{name}".upper())
    return names


def test_the_root_example_exists() -> None:
    assert ROOT_EXAMPLE.is_file(), "a new developer starts by copying .env.example to .env"


@pytest.mark.parametrize("variable", sorted(settings_variables()))
def test_every_setting_is_documented(variable: str) -> None:
    assert variable in keys_of(ROOT_EXAMPLE), (
        f"{variable} is read by tcg_api.config.Settings but absent from .env.example"
    )


def test_no_documented_api_variable_is_unread() -> None:
    documented = {key for key in keys_of(ROOT_EXAMPLE) if key.startswith("TCG_API_")}

    assert documented <= settings_variables(), (
        "these keys are in .env.example but no setting reads them: "
        f"{sorted(documented - settings_variables())}"
    )


def test_the_web_variables_are_documented_too() -> None:
    """One file starts the whole stack, so it covers both workspaces."""
    missing = set(keys_of(WEB_EXAMPLE)) - set(keys_of(ROOT_EXAMPLE))

    assert not missing, f"apps/web/.env.example declares {sorted(missing)}, the root does not"
