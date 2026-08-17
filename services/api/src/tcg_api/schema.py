"""Emit the OpenAPI schema as JSON on stdout.

Per `docs/adr/0001-language-boundaries-in-the-monorepo.md` the frontend/backend
contract *is* this schema: `apps/web` generates its TypeScript types from it
rather than importing a shared package. That makes the schema a build input, so
it needs a way to be produced without starting a server — this module is it.

The ADR notes that this raises the cost of an undocumented endpoint,
deliberately. A route with no response model produces no usable type, and the
frontend cannot then call it without hand-writing one.

Usage::

    uv run tcg-api-schema > openapi.json
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

from tcg_api.app import create_app


def openapi_schema() -> dict[str, Any]:
    """Build the application and return its OpenAPI document.

    Constructing the app is enough; no server is started and nothing is bound
    to a port, so this is safe to run in CI and in a pre-commit hook.

    `create_app` configures structured logging and emits a startup event, and
    that goes to stdout — correct for a container, corrupting for a command
    whose output is piped into a code generator. Redirecting to stderr keeps
    the log visible to a human while leaving stdout as pure JSON.
    """
    with contextlib.redirect_stdout(sys.stderr):
        app = create_app()
        return app.openapi()


def main() -> int:
    """Write the schema to stdout as stable, diffable JSON."""
    # `sort_keys` and a fixed indent are what make the generated TypeScript
    # reproducible: without them, dictionary ordering could churn the output
    # and the drift check in CI would fail for no real reason.
    json.dump(openapi_schema(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
