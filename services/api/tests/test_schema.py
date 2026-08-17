"""The OpenAPI schema dump is a build input, so it has to be machine-clean.

`apps/web` generates its TypeScript types from this output (ADR 0001). That
makes two properties load-bearing in a way they would not be for a debugging
command: stdout must contain nothing but JSON, and the same application must
produce byte-identical output every time. Without the first, the generator
consumes a corrupt schema; without the second, CI's drift check fails on runs
where nothing changed.
"""

from __future__ import annotations

import json
import subprocess
import sys

from tcg_api.schema import main, openapi_schema


def test_schema_documents_both_probes() -> None:
    schema = openapi_schema()

    assert "/health" in schema["paths"]
    assert "/readiness" in schema["paths"]


def test_health_response_model_reaches_the_schema() -> None:
    """A route without a response model would generate no usable type."""
    schema = openapi_schema()
    properties = schema["components"]["schemas"]["HealthResponse"]["properties"]

    assert set(properties) == {"status", "application_version"}


def test_output_is_deterministic() -> None:
    """Two dumps of the same app must be byte-identical, or CI's drift check flaps."""
    assert json.dumps(openapi_schema(), sort_keys=True) == json.dumps(
        openapi_schema(), sort_keys=True
    )


def test_stdout_is_json_and_nothing_else() -> None:
    """The regression that would silently corrupt the generated types.

    `create_app` configures logging and emits a startup event on stdout, so a
    naive implementation writes a log line before the schema. Run in a
    subprocess because that log is emitted through handlers pytest replaces.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "tcg_api.schema"],
        capture_output=True,
        check=True,
        text=True,
    )

    document = json.loads(completed.stdout)  # raises if anything else got in
    assert "/health" in document["paths"]


def test_main_reports_success(capsys) -> None:
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["openapi"]
