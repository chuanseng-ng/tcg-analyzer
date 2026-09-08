"""Nothing in `ml/*` can reach a user's reported grade — #270, spec §68.

A claim about the repository rather than about a package, which is why it lives
here — `test_adapters_carry_the_predictors.py`'s reason. `services/api`'s own
`test_import_purity.py` holds the other half, that nothing under
`tcg_api.datasets` imports the feedback domain; this one holds the `ml/*` side,
which that suite cannot make because the two are separate distributions and the
API tests do not import the model packages.

**A static scan rather than a runtime probe**, because the rule is stronger than
"does not import it today": no `ml/*` package declares a dependency on
`services/api` at all, so an import of `tcg_api` anywhere in one would not even
resolve. Asserting the absence in the source is what makes it a rule somebody
has to break deliberately.

**`ast`, not a grep.** Four `ml/*` modules mention `tcg_api` in prose — the
image-quality gate's docstring names the caller, and three `__init__.py` files
explain who consumes them — so a text search would fail on documentation that is
doing exactly what it should.

Spec §68's diagram is `user feedback → validation → approved dataset → future
training`. The arrow this file forbids is the one straight from the first box to
the last.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every Python source file in every model package.
ML_SOURCES = sorted(REPO_ROOT.glob("ml/*/src/**/*.py")) + sorted(
    REPO_ROOT.glob("ml/*/*/src/**/*.py")
)

#: Every model package's manifest. `ml/grading/*` is a directory deeper.
ML_MANIFESTS = sorted(REPO_ROOT.glob("ml/*/pyproject.toml")) + sorted(
    REPO_ROOT.glob("ml/*/*/pyproject.toml")
)


def _imported_names(source: Path) -> set[str]:
    """Every module name this file imports, from the syntax tree.

    A relative import contributes nothing: it cannot reach another
    distribution, which is the only thing this file is asking about.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_the_scan_reaches_every_model_package() -> None:
    """Guard the guard: a glob that matched nothing would pass every test below."""
    packages = {source.relative_to(REPO_ROOT).parts[1] for source in ML_SOURCES}

    assert len(ML_SOURCES) > 30
    assert len(ML_MANIFESTS) >= 12
    assert {"condition", "grading", "image-quality"} <= packages


def test_no_model_package_imports_the_service() -> None:
    """The wall, stated where somebody would have to break it on purpose.

    `ml/*` answers "what grade might this receive?" from a `ConditionAssessment`
    and nothing else. A model that could read `tcg_api.feedback` would be one
    training step away from the automatic retraining spec §68 forbids, and a
    model that could read `tcg_api` at all has stopped being replaceable.
    """
    offenders = {
        str(source.relative_to(REPO_ROOT)): sorted(
            name for name in _imported_names(source) if name.split(".")[0] == "tcg_api"
        )
        for source in ML_SOURCES
    }
    offenders = {path: names for path, names in offenders.items() if names}

    assert offenders == {}, (
        f"{offenders} import from the API service. Spec §68 puts an operator's "
        "validation between a user's reported grade and any future training, and "
        "the architecture keeps the model packages free of the service entirely."
    )


def test_no_model_package_depends_on_the_service() -> None:
    """The same rule one level up, where a dependency would be declared."""
    offenders: dict[str, list[str]] = {}
    for manifest in ML_MANIFESTS:
        declared = tomllib.loads(manifest.read_text(encoding="utf-8"))
        requirements = list(declared.get("project", {}).get("dependencies", []))
        for extra in declared.get("project", {}).get("optional-dependencies", {}).values():
            requirements.extend(extra)
        named = [
            requirement
            for requirement in requirements
            if requirement.replace("_", "-").startswith("tcg-api")
        ]
        if named:
            offenders[str(manifest.relative_to(REPO_ROOT))] = named

    assert offenders == {}, (
        f"{offenders} declare a dependency on the API service. The dependency "
        "runs one way: the worker imports the models, and never the reverse."
    )
