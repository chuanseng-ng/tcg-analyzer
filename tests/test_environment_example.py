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
#: Discovered rather than listed. The point of the check below is that a *new*
#: application cannot introduce a variable the root example does not document,
#: and a hardcoded list fails open for exactly the app nobody remembered to add.
APP_EXAMPLES = tuple(sorted((REPO_ROOT / "apps").glob("*/.env.example")))

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


@pytest.mark.parametrize("example", APP_EXAMPLES, ids=lambda path: path.parent.name)
def test_the_app_variables_are_documented_too(example: Path) -> None:
    """One file starts the whole stack, so it covers both workspaces."""
    missing = set(keys_of(example)) - set(keys_of(ROOT_EXAMPLE))
    name = example.relative_to(REPO_ROOT).as_posix()

    assert not missing, f"{name} declares {sorted(missing)}, the root does not"


def test_every_app_carries_an_environment_example() -> None:
    """Guard the guard: parametrizing over a glob that found nothing passes vacuously."""
    apps = {path.name for path in (REPO_ROOT / "apps").iterdir() if path.is_dir()}

    assert {example.parent.name for example in APP_EXAMPLES} == apps
