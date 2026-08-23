"""Check the committed documentation against the repository it describes.

The specification and `CLAUDE.md` are both gitignored, so `README.md`,
`CONTRIBUTING.md` and `docs/` are the only instructions a contributor actually
receives. A command that no longer works, or a link to a renamed file, is
therefore not a cosmetic defect: it is the whole onboarding path breaking, and
it breaks silently, because nothing else in the suite reads prose.

The checks here are *static*. They resolve each documented command against the
repository — is that a real pnpm script, a registered pytest marker, a Compose
service that exists — rather than executing it. Executing them would need
Docker, PostgreSQL and MinIO, would take minutes, and would duplicate the CI
jobs that already start the stack for real. Static resolution catches the
failure that actually happens in practice: a rename on one side of the
documentation boundary and not the other.

Every check is parametrized so a failure names the offending command rather than
reporting that "the README is wrong".
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# The documents a contributor is expected to follow. `docs/architecture.md` has
# no commands but plenty of links, so it joins the link check further down.
DOCUMENTED_COMMAND_SOURCES: tuple[str, ...] = ("README.md", "CONTRIBUTING.md")
LINKED_DOCUMENTS: tuple[str, ...] = (
    "README.md",
    "CONTRIBUTING.md",
    "docs/README.md",
    "docs/architecture.md",
    "docs/market-provider-research.md",
    "docs/retention.md",
)

_BASH_BLOCK = re.compile(r"^```bash\n(.*?)^```", re.MULTILINE | re.DOTALL)
_TRAILING_COMMENT = re.compile(r"\s+#.*$")
# Inline links, excluding images. Autolinks (<http://…>) are deliberately not
# matched: they are external by construction and nothing here can verify them.
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _commands_in(relative_path: str) -> list[list[str]]:
    """Every shell command in the document's ```bash blocks, tokenized."""
    commands: list[list[str]] = []
    for block in _BASH_BLOCK.findall(_read(relative_path)):
        for raw_line in block.splitlines():
            line = _TRAILING_COMMENT.sub("", raw_line).strip()
            if not line or line.startswith("#"):
                continue
            for fragment in line.split("&&"):
                tokens = shlex.split(fragment)
                if tokens:
                    commands.append(tokens)
    return commands


ALL_COMMANDS: list[tuple[str, list[str]]] = [
    (source, command) for source in DOCUMENTED_COMMAND_SOURCES for command in _commands_in(source)
]


def _matching(*prefix: str) -> list[tuple[str, list[str]]]:
    return [
        (source, command)
        for source, command in ALL_COMMANDS
        if tuple(command[: len(prefix)]) == prefix
    ]


def _identify(case: tuple[str, list[str]]) -> str:
    source, command = case
    return f"{source}: {shlex.join(command)}"


# --------------------------------------------------------------------------
# pnpm scripts
# --------------------------------------------------------------------------
PNPM_COMMANDS = _matching("pnpm", "--filter", "@tcg/web")
# Subcommands that take a script name are the point of this check; `exec`,
# `install` and `add` take something else entirely.
_NOT_A_SCRIPT = frozenset({"exec", "install", "add", "run", "dlx"})


@pytest.mark.parametrize("case", PNPM_COMMANDS, ids=_identify)
def test_documented_pnpm_script_exists(case: tuple[str, list[str]]) -> None:
    _, command = case
    script = command[3]
    if script in _NOT_A_SCRIPT:
        pytest.skip(f"`pnpm {script}` does not name a package script")

    manifest = json.loads(_read("apps/web/package.json"))
    assert script in manifest["scripts"], (
        f"apps/web/package.json has no `{script}` script; documentation is stale"
    )


# --------------------------------------------------------------------------
# Compose files and the services documented against them
# --------------------------------------------------------------------------
COMPOSE_COMMANDS = _matching("docker", "compose")
_COMPOSE_SUBCOMMANDS_TAKING_SERVICES = frozenset(
    {"up", "logs", "ps", "restart", "stop", "run", "watch"}
)


def _compose_file_of(command: list[str]) -> Path:
    return REPO_ROOT / command[command.index("-f") + 1]


@pytest.mark.parametrize("case", COMPOSE_COMMANDS, ids=_identify)
def test_documented_compose_file_exists(case: tuple[str, list[str]]) -> None:
    _, command = case
    assert "-f" in command, "documented `docker compose` calls name their file explicitly"
    assert _compose_file_of(command).is_file()


@pytest.mark.parametrize("case", COMPOSE_COMMANDS, ids=_identify)
def test_documented_compose_services_exist(case: tuple[str, list[str]]) -> None:
    """A service argument must be a service the Compose file actually defines."""
    _, command = case
    compose = yaml.safe_load(_compose_file_of(command).read_text(encoding="utf-8"))
    defined = set(compose.get("services", {}))

    tail = command[command.index("-f") + 2 :]
    if not tail or tail[0] not in _COMPOSE_SUBCOMMANDS_TAKING_SERVICES:
        return

    for argument in tail[1:]:
        if argument.startswith("-"):
            continue
        assert argument in defined, (
            f"`{argument}` is not a service in {command[command.index('-f') + 1]}"
        )


# --------------------------------------------------------------------------
# pytest markers
# --------------------------------------------------------------------------
PYTEST_COMMANDS = [case for case in _matching("uv", "run", "pytest") if "-m" in case[1]]
_MARKER_KEYWORDS = frozenset({"not", "and", "or"})


@pytest.mark.parametrize("case", PYTEST_COMMANDS, ids=_identify)
def test_documented_pytest_markers_are_registered(case: tuple[str, list[str]]) -> None:
    """Documenting an unregistered marker documents a command that selects nothing."""
    _, command = case
    expression = command[command.index("-m") + 1]
    named = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression)) - _MARKER_KEYWORDS

    config = tomllib.loads(_read("pyproject.toml"))["tool"]["pytest"]["ini_options"]
    registered = {entry.split(":", 1)[0] for entry in config["markers"]}

    assert named <= registered, (
        f"unregistered pytest marker(s) {sorted(named - registered)} — "
        "add them to [tool.pytest.ini_options] markers or fix the documentation"
    )


# --------------------------------------------------------------------------
# Paths named as arguments
# --------------------------------------------------------------------------
def _documented_paths() -> list[tuple[str, str]]:
    """(source document, repository-relative path) for every path a command names."""
    paths: list[tuple[str, str]] = []
    for source, command in ALL_COMMANDS:
        if command[:3] == ["uv", "run", "mypy"]:
            paths += [
                (source, argument) for argument in command[3:] if not argument.startswith("-")
            ]
        elif command[:2] == ["docker", "build"] and "-f" in command:
            paths.append((source, command[command.index("-f") + 1]))
        elif command[0] == "cp" and len(command) == 3:
            paths.append((source, command[1]))
    return paths


@pytest.mark.parametrize(
    ("source", "path"), _documented_paths(), ids=lambda value: str(value).replace("/", "-")
)
def test_documented_path_exists(source: str, path: str) -> None:
    assert (REPO_ROOT / path).exists(), f"{source} names {path}, which does not exist"


# --------------------------------------------------------------------------
# Environment variables
# --------------------------------------------------------------------------
EXPORTED_VARIABLES = sorted(
    {
        command[1].split("=", 1)[0]
        for _, command in ALL_COMMANDS
        if command[0] == "export" and len(command) > 1 and "=" in command[1]
    }
)


@pytest.mark.parametrize("variable", EXPORTED_VARIABLES)
def test_documented_environment_variable_is_in_the_example(variable: str) -> None:
    """A variable worth exporting in the README is worth listing in `.env.example`.

    `tests/test_environment_example.py` guards the other direction — settings
    that exist in code but not in the example. Together they close the loop.
    """
    example = _read(".env.example")
    assert re.search(rf"^#?\s*{re.escape(variable)}=", example, re.MULTILINE), (
        f"{variable} is exported in the documentation but absent from .env.example"
    )


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------
def _slug(heading: str) -> str:
    """GitHub's heading anchor, near enough for links inside this repository."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def _anchors_in(document: Path) -> set[str]:
    return {
        _slug(match)
        for match in re.findall(
            r"^#{1,6}\s+(.*)$", document.read_text(encoding="utf-8"), re.MULTILINE
        )
    }


def _relative_links() -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for source in LINKED_DOCUMENTS:
        for target in _MARKDOWN_LINK.findall(_read(source)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            links.append((source, target))
    return links


@pytest.mark.parametrize(
    ("source", "target"), _relative_links(), ids=lambda value: str(value).replace("/", "-")
)
def test_documentation_link_resolves(source: str, target: str) -> None:
    path, _, fragment = target.partition("#")
    resolved = (REPO_ROOT / source).parent / path

    assert resolved.exists(), f"{source} links to {target}, which does not exist"

    if fragment and resolved.is_file():
        assert fragment in _anchors_in(resolved), (
            f"{source} links to {target}, but {path} has no such heading"
        )


# --------------------------------------------------------------------------
# Guard the guards
# --------------------------------------------------------------------------
def test_the_documented_commands_were_actually_found() -> None:
    """A parser that silently stops matching would make every check above vacuous.

    The same defence as `test_the_tracked_file_listing_is_not_silently_empty` in
    `tests/test_repository_structure.py`: assert the input is non-trivial, so
    that a regex which quietly matches nothing fails loudly here instead of
    turning the rest of this module green.
    """
    assert len(ALL_COMMANDS) >= 25
    assert len(PNPM_COMMANDS) >= 5
    assert len(COMPOSE_COMMANDS) >= 2
    assert len(PYTEST_COMMANDS) >= 2
    assert len(_relative_links()) >= 10
