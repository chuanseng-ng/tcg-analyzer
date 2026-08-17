"""The local stack is M0's acceptance criterion, so its shape is asserted.

`docker compose up` bringing up the whole application is the one thing
milestone M0 promises a new developer. The obvious test for it needs Docker and
several minutes, so it lives in CI (the `compose` job) rather than here. What
this file covers is the part that breaks quietly: the wiring.

Every assertion below corresponds to a way the stack has a plausible chance of
being silently wrong — a service that starts before its database is ready, a
browser handed a hostname only a container can resolve, a container that keeps
the privileges spec §56 says it must not. None of them would fail a syntax
check, and several would still appear to work on the machine of whoever
introduced them.

The file is read as data rather than as text so that reformatting it, or
reordering its keys, does not fail the suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "infrastructure" / "local" / "docker-compose.yml"

#: Every service a developer expects after one `up`. `migrate` is deliberately
#: included: it is not a service in the running sense, but its absence would
#: mean the database is never migrated, which is the failure this list guards.
EXPECTED_SERVICES = frozenset({"postgres", "minio", "migrate", "api", "web"})

#: The services whose images this repository builds, and which are therefore
#: the ones it can hold to the non-root, least-privilege baseline. The
#: PostgreSQL image drops to its own `postgres` user internally; the MinIO
#: image runs as root and pinning `user:` would fight its named volume's
#: ownership on first start. Both are upstream images, and neither is something
#: this file can honestly claim to have hardened.
BUILT_SERVICES = frozenset({"migrate", "api", "web"})


def compose() -> dict[str, Any]:
    """The Compose file, parsed.

    `safe_load` resolves the merge keys, so a service that inherits an anchor
    is seen here exactly as Compose sees it.
    """
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    return compose()["services"]


def test_the_compose_file_exists() -> None:
    assert COMPOSE_FILE.is_file(), (
        "infrastructure/local/docker-compose.yml is the single local stack; "
        "there is deliberately no second Compose file"
    )


def test_every_expected_service_is_declared(services: dict[str, Any]) -> None:
    assert set(services) == set(EXPECTED_SERVICES)


# ---------------------------------------------------------------------------
# Startup ordering
#
# `depends_on` without a condition waits only for the container to be created,
# which for a database means "not ready". Each assertion below is a race that
# passes on a warm machine and fails on a cold one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dependency", ["postgres", "minio"])
def test_the_api_waits_for_its_dependencies_to_be_healthy(
    services: dict[str, Any], dependency: str
) -> None:
    assert services["api"]["depends_on"][dependency]["condition"] == "service_healthy"


def test_the_api_waits_for_migrations_to_have_finished(services: dict[str, Any]) -> None:
    """Not merely started — `service_completed_successfully` is the whole point.

    A failed migration must stop the API from starting, rather than leave it
    serving against a schema that was never applied.
    """
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_migrations_wait_for_the_database_to_be_healthy(services: dict[str, Any]) -> None:
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_the_web_app_waits_for_the_api_to_be_healthy(services: dict[str, Any]) -> None:
    assert services["web"]["depends_on"]["api"]["condition"] == "service_healthy"


def test_the_migration_service_does_not_restart(services: dict[str, Any]) -> None:
    """It has finished when it exits; restarting re-runs `upgrade head` forever."""
    assert services["migrate"]["restart"] == "no"


def test_the_migration_service_actually_migrates(services: dict[str, Any]) -> None:
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]


def test_migrations_run_from_the_api_image(services: dict[str, Any]) -> None:
    """So the migrations that run cannot drift from the code reading the schema."""
    assert services["migrate"]["image"] == services["api"]["image"]
    assert services["migrate"]["build"] == services["api"]["build"]


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


def test_the_browser_is_given_a_url_it_can_actually_resolve(services: dict[str, Any]) -> None:
    """`ApiStatus` is a client component, so this URL is used by the browser.

    `http://api:8000` resolves inside the Compose network and nowhere else. It
    would work in every container-to-container test and fail for every human.
    """
    base_url = services["web"]["environment"]["NEXT_PUBLIC_API_BASE_URL"]

    assert base_url.startswith("http://localhost:"), base_url


@pytest.mark.parametrize(
    ("variable", "expected_host"),
    [("TCG_API_DATABASE_URL", "@postgres:5432/"), ("TCG_API_STORAGE_ENDPOINT_URL", "minio:9000")],
)
def test_the_api_reaches_its_dependencies_by_service_name(
    services: dict[str, Any], variable: str, expected_host: str
) -> None:
    """Inside the network, `localhost` is the container itself."""
    assert expected_host in services["api"]["environment"][variable]


def test_the_published_api_port_and_the_cors_origin_agree(services: dict[str, Any]) -> None:
    """A `WEB_PORT` override must not silently break CORS for the moved app.

    Both are written in terms of the same variables, so this asserts that the
    default they fall back to is the same one.
    """
    assert "${WEB_PORT:-3000}" in services["api"]["environment"]["TCG_API_CORS_ORIGINS"]
    assert "${API_PORT:-8000}" in services["web"]["environment"]["NEXT_PUBLIC_API_BASE_URL"]


# ---------------------------------------------------------------------------
# Hardening — spec §56
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", sorted(EXPECTED_SERVICES))
def test_no_container_can_gain_new_privileges(services: dict[str, Any], service: str) -> None:
    assert "no-new-privileges:true" in services[service]["security_opt"]


@pytest.mark.parametrize("service", sorted(BUILT_SERVICES))
def test_every_image_this_repository_builds_runs_unprivileged(service: str) -> None:
    """Asserted against the Dockerfile, because Compose cannot show a `USER`.

    Spec §56 requires the ML worker to run with minimal privileges. That worker
    arrives in M6; the baseline it will inherit is established here, while there
    is nothing to break.
    """
    dockerfile = REPO_ROOT / compose()["services"][service]["build"]["dockerfile"]

    assert "USER tcg" in dockerfile.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Hot reload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["api", "web"])
def test_source_changes_are_synced_into_the_running_container(
    services: dict[str, Any], service: str
) -> None:
    actions = {rule["action"] for rule in services[service]["develop"]["watch"]}

    assert "sync" in actions


@pytest.mark.parametrize(("service", "lockfile"), [("api", "uv.lock"), ("web", "pnpm-lock.yaml")])
def test_a_dependency_change_rebuilds_rather_than_syncs(
    services: dict[str, Any], service: str, lockfile: str
) -> None:
    """A new dependency cannot be copied into an already-resolved environment."""
    rebuilt = {
        rule["path"]
        for rule in services[service]["develop"]["watch"]
        if rule["action"] == "rebuild"
    }

    assert any(path.endswith(lockfile) for path in rebuilt), rebuilt


def test_the_web_sync_does_not_clobber_the_installed_dependencies(
    services: dict[str, Any],
) -> None:
    """`apps/web/node_modules` exists in the image and must not be synced over.

    The host's copy holds binaries built for the host, and `.dockerignore`
    keeps it out of the build context for the same reason.
    """
    sync = next(rule for rule in services["web"]["develop"]["watch"] if rule["action"] == "sync")

    assert "node_modules/" in sync.get("ignore", [])
