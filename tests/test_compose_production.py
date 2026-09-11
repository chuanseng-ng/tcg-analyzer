"""The production overlay (#273) is the file a host runs, so its merged shape is asserted.

`test_compose_stack.py` reads the local file as YAML because that file stands
alone. The overlay does not: a host runs `-f local -f prod`, and what it runs is
the merge — `!reset` tags, per-key environment overrides and profiles included.
Reimplementing Compose's merge here would be a second definition of it, so
Compose itself renders the stack (`config` needs no daemon) and the assertions
read what it rendered.

Every assertion is a line of #263's §56 checklist or a way the overlay could be
quietly wrong: a datastore still published because a port list was merged
rather than reset, a worker with egress because a service fell onto `default`,
an application still holding MinIO's root credentials because the override
missed one key.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FILE = REPO_ROOT / "infrastructure" / "local" / "docker-compose.yml"
OVERLAY_FILE = REPO_ROOT / "infrastructure" / "deployment" / "docker-compose.prod.yml"
CADDYFILE = REPO_ROOT / "infrastructure" / "deployment" / "Caddyfile"

#: The overlay's own set, deliberately not `test_compose_stack.EXPECTED_SERVICES`:
#: that one is the local file's, and growing it would make the local stack
#: assert services it does not have.
EXPECTED_SERVICES = frozenset(
    {
        "postgres",
        "minio",
        "minio-init",
        "redis",
        "certs",
        "migrate",
        "api",
        "worker",
        "web",
        "annotation",
        "catalog-import",
        "proxy",
    }
)

#: Started only when asked for. Annotation is a browser client of `/internal`,
#: which nothing outside this host can reach (ADR 0009); the catalog import is
#: an operator command that needs the one route out the application lacks.
OPT_IN = ("annotation", "catalog-import")

#: Run to completion by `up`, and waited for because something depends on each.
ONE_SHOTS = ("certs", "minio-init", "migrate")

#: Exactly the networks each service joins. `backend` and `edge` are internal;
#: only `public` reaches outside, and only the proxy and the operator's catalog
#: import are on it. `certs` is absent because it has no network at all.
EXPECTED_NETWORKS = {
    "postgres": {"backend"},
    "minio": {"backend"},
    "minio-init": {"backend"},
    "redis": {"backend"},
    "migrate": {"backend"},
    "worker": {"backend"},
    "annotation": {"backend"},
    "api": {"backend", "edge"},
    "web": {"edge"},
    "proxy": {"edge", "public"},
    "catalog-import": {"backend", "public"},
}

#: Distinct from each other and from the local file's defaults, so an assertion
#: that a key "is the app key" cannot pass by coincidence.
DUMMY_ENV = {
    "TCG_DOMAIN": "beta.example.test",
    "POSTGRES_PASSWORD": "pg-dummy-0001",
    "MINIO_ROOT_USER": "root-dummy",
    "MINIO_ROOT_PASSWORD": "root-secret-dummy-0002",
    "MINIO_APP_ACCESS_KEY": "app-dummy",
    "MINIO_APP_SECRET_KEY": "app-secret-dummy-0003",
    "REDIS_PASSWORD": "redis-dummy-0004",
}

#: The tmpfs inside the worker container — a path on the container's filesystem,
#: never on the machine running this suite, which is what bandit's S108 asks.
CONTAINER_TMP = "/tmp"  # noqa: S108

#: Anything in the developer's shell that the files interpolate. Left in, a
#: local `REDIS_PASSWORD` would make these assertions about that machine.
_INTERPOLATED_PREFIXES = ("TCG_", "POSTGRES_", "MINIO_", "REDIS_", "COMPOSE_", "NEXT_PUBLIC_")
_INTERPOLATED_PORTS = ("API_PORT", "WEB_PORT", "ANNOTATION_PORT")


def _docker() -> str:
    docker = shutil.which("docker")
    if docker is None:
        if os.environ.get("CI"):
            pytest.fail("docker is on every CI runner; without it this file checked nothing")
        pytest.skip("docker is not installed, and the overlay is rendered by Compose itself")
    return docker


def render(
    env: dict[str, str], directory: Path, *profiles: str
) -> subprocess.CompletedProcess[str]:
    """`docker compose config` over both files, reading only `env`."""
    env_file = directory / ".env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_INTERPOLATED_PREFIXES) and key not in _INTERPOLATED_PORTS
    }
    selected = [argument for profile in profiles for argument in ("--profile", profile)]
    return subprocess.run(
        [
            _docker(),
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(LOCAL_FILE),
            "-f",
            str(OVERLAY_FILE),
            *selected,
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=120,
    )


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    result = render(DUMMY_ENV, tmp_path_factory.mktemp("overlay"), *OPT_IN)
    assert result.returncode == 0, result.stderr
    rendered: dict[str, Any] = json.loads(result.stdout)
    return rendered


@pytest.fixture(scope="module")
def services(stack: dict[str, Any]) -> dict[str, Any]:
    rendered: dict[str, Any] = stack["services"]
    return rendered


def test_every_expected_service_is_declared(services: dict[str, Any]) -> None:
    assert set(services) == set(EXPECTED_SERVICES)


def test_a_plain_up_starts_neither_opt_in_service(tmp_path: Path) -> None:
    """Annotation off by default is the decision; a profile is how it holds."""
    result = render(DUMMY_ENV, tmp_path)

    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)["services"]) == set(EXPECTED_SERVICES) - set(OPT_IN)


def test_the_project_is_not_the_local_one(stack: dict[str, Any]) -> None:
    """A shared project name would let `down -v` on one stack destroy the other's volumes."""
    assert stack["name"] == "tcg-analyzer"


@pytest.mark.parametrize("variable", sorted(DUMMY_ENV))
def test_a_missing_secret_stops_the_stack(tmp_path: Path, variable: str) -> None:
    """The local file's obvious defaults must never reach a host by omission."""
    env = {key: value for key, value in DUMMY_ENV.items() if key != variable}

    result = render(env, tmp_path)

    assert result.returncode != 0
    assert variable in result.stderr


# ---------------------------------------------------------------------------
# Networks — checklist items 1-3
# ---------------------------------------------------------------------------


def test_every_service_but_the_certificate_job_has_its_networks_listed() -> None:
    assert set(EXPECTED_NETWORKS) | {"certs"} == EXPECTED_SERVICES


@pytest.mark.parametrize("service", sorted(EXPECTED_NETWORKS))
def test_each_service_is_on_exactly_its_networks(services: dict[str, Any], service: str) -> None:
    assert set(services[service].get("networks", {})) == EXPECTED_NETWORKS[service]


def test_the_certificate_job_has_no_network(services: dict[str, Any]) -> None:
    assert services["certs"]["network_mode"] == "none"


def test_only_the_public_network_reaches_outside(stack: dict[str, Any]) -> None:
    """No `default` network, and the two the application lives on have no egress."""
    networks = stack["networks"]

    assert set(networks) == {"backend", "edge", "public"}
    assert networks["backend"]["internal"] is True
    assert networks["edge"]["internal"] is True
    assert not networks["public"].get("internal", False)


def test_only_the_proxy_publishes_a_port(services: dict[str, Any]) -> None:
    """`ports` lists merge by concatenation, so each one in the local file is reset."""
    assert {name for name, service in services.items() if service.get("ports")} == {"proxy"}


def test_the_proxy_publishes_http_https_and_http3(services: dict[str, Any]) -> None:
    published = {
        (str(port["published"]), port.get("protocol", "tcp")) for port in services["proxy"]["ports"]
    }

    assert published == {("80", "tcp"), ("443", "tcp"), ("443", "udp")}


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def test_no_service_runs_a_development_image(services: dict[str, Any]) -> None:
    """A production build under a `:dev` tag would be swapped into the local stack."""
    assert not [name for name, service in services.items() if service["image"].endswith(":dev")]


@pytest.mark.parametrize("service", ["migrate", "certs", "catalog-import"])
def test_the_api_image_serves_the_jobs_too(services: dict[str, Any], service: str) -> None:
    assert services[service]["image"] == services["api"]["image"]


@pytest.mark.parametrize("service", ["web", "annotation"])
def test_the_next_applications_run_their_production_stage(
    services: dict[str, Any], service: str
) -> None:
    assert services[service]["build"]["target"] == "production"


def test_the_web_api_url_is_a_build_argument(services: dict[str, Any]) -> None:
    """Next inlines `NEXT_PUBLIC_*` at build time; a run-time value reaches nothing."""
    assert (
        services["web"]["build"]["args"]["NEXT_PUBLIC_API_BASE_URL"]
        == f"https://{DUMMY_ENV['TCG_DOMAIN']}/api"
    )


@pytest.mark.parametrize("service", ["web", "annotation"])
def test_no_api_url_is_set_at_run_time(services: dict[str, Any], service: str) -> None:
    assert "NEXT_PUBLIC_API_BASE_URL" not in (services[service].get("environment") or {})


def test_the_proxy_image_is_pinned_by_digest(services: dict[str, Any]) -> None:
    assert re.fullmatch(
        r"caddy:\d+\.\d+\.\d+-alpine@sha256:[0-9a-f]{64}", services["proxy"]["image"]
    )


# ---------------------------------------------------------------------------
# Hardening — checklist items 4-6
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", sorted(EXPECTED_SERVICES))
def test_no_container_can_gain_new_privileges(services: dict[str, Any], service: str) -> None:
    assert "no-new-privileges:true" in services[service]["security_opt"]


def test_the_worker_root_filesystem_is_read_only(services: dict[str, Any]) -> None:
    worker = services["worker"]

    assert worker["read_only"] is True
    assert CONTAINER_TMP in worker["tmpfs"]


def test_beat_keeps_its_schedule_on_the_tmpfs(services: dict[str, Any]) -> None:
    command = services["worker"]["command"]

    assert command[command.index("--schedule") + 1].startswith(f"{CONTAINER_TMP}/")


def test_the_worker_is_bounded(services: dict[str, Any]) -> None:
    worker = services["worker"]

    assert worker["mem_limit"]
    assert worker["pids_limit"] > 0
    assert worker["cap_drop"] == ["ALL"]


def test_there_is_one_worker(services: dict[str, Any]) -> None:
    """Beat is embedded, so a second replica is a second scheduler."""
    assert services["worker"].get("scale", 1) == 1
    assert services["worker"].get("deploy", {}).get("replicas", 1) == 1


def test_the_proxy_keeps_one_capability(services: dict[str, Any]) -> None:
    """The Caddy binary carries a file capability; exec fails if it cannot be granted."""
    assert services["proxy"]["cap_drop"] == ["ALL"]
    assert services["proxy"]["cap_add"] == ["NET_BIND_SERVICE"]


def test_the_certificate_job_can_only_hand_the_key_over(services: dict[str, Any]) -> None:
    assert services["certs"]["cap_drop"] == ["ALL"]
    assert services["certs"]["cap_add"] == ["CHOWN"]


def test_the_ca_key_does_not_outlive_the_certificate_job(services: dict[str, Any]) -> None:
    """An exited container keeps its writable layer; a tmpfs does not survive it."""
    assert CONTAINER_TMP in services["certs"]["tmpfs"]


# ---------------------------------------------------------------------------
# Credentials — checklist items 7-10
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["api", "worker"])
def test_the_application_holds_the_scoped_storage_account(
    services: dict[str, Any], service: str
) -> None:
    environment = services[service]["environment"]

    assert environment["TCG_API_STORAGE_ACCESS_KEY_ID"] == DUMMY_ENV["MINIO_APP_ACCESS_KEY"]
    assert environment["TCG_API_STORAGE_SECRET_ACCESS_KEY"] == DUMMY_ENV["MINIO_APP_SECRET_KEY"]
    assert DUMMY_ENV["MINIO_ROOT_PASSWORD"] not in json.dumps(environment)


@pytest.mark.parametrize("service", ["api", "worker"])
def test_the_broker_is_reached_over_verified_tls(services: dict[str, Any], service: str) -> None:
    url = services[service]["environment"]["TCG_API_REDIS_URL"]

    assert url.startswith(f"rediss://:{DUMMY_ENV['REDIS_PASSWORD']}@redis:6379/"), url
    assert "ssl_cert_reqs=required" in url
    assert "ssl_ca_certs=/certs/ca.crt" in url


@pytest.mark.parametrize("service", ["api", "worker"])
def test_the_ca_certificate_is_mounted_read_only(services: dict[str, Any], service: str) -> None:
    mounts = {
        (volume["source"], volume["target"], volume.get("read_only", False))
        for volume in services[service]["volumes"]
    }

    assert ("redis-certs", "/certs", True) in mounts


@pytest.mark.parametrize("service", ["api", "worker", "migrate", "catalog-import"])
def test_the_database_password_is_the_hosts(services: dict[str, Any], service: str) -> None:
    url = services[service]["environment"]["TCG_API_DATABASE_URL"]

    assert f":{DUMMY_ENV['POSTGRES_PASSWORD']}@postgres:5432/" in url


def test_the_api_knows_it_is_behind_one_https_proxy(services: dict[str, Any]) -> None:
    environment = services["api"]["environment"]

    assert environment["TCG_API_TRUSTED_PROXY_COUNT"] == "1"
    assert environment["TCG_API_SESSION_COOKIE_SECURE"] == "true"
    assert json.loads(environment["TCG_API_CORS_ORIGINS"]) == [f"https://{DUMMY_ENV['TCG_DOMAIN']}"]


def test_the_api_does_not_hot_reload(services: dict[str, Any]) -> None:
    """Reset to the image's own CMD: plain uvicorn, no `--reload`.

    Compose renders a reset `command` as `null`, which is "use the image's".
    """
    assert services["api"].get("command") is None


@pytest.mark.parametrize("service", ["migrate", "api", "worker"])
def test_the_stack_logs_json(services: dict[str, Any], service: str) -> None:
    assert services[service]["environment"]["TCG_API_LOG_FORMAT"] == "json"


# ---------------------------------------------------------------------------
# Startup ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ONE_SHOTS)
def test_a_one_shot_is_not_restarted(services: dict[str, Any], service: str) -> None:
    assert services[service]["restart"] == "no"


def test_every_one_shot_is_awaited(services: dict[str, Any]) -> None:
    """`up --wait` treats an exited container as a failure unless something waits on it."""
    awaited = {
        dependency
        for service in services.values()
        for dependency, condition in service.get("depends_on", {}).items()
        if condition["condition"] == "service_completed_successfully"
    }

    assert set(ONE_SHOTS) <= awaited


def test_redis_waits_for_its_certificate(services: dict[str, Any]) -> None:
    assert services["redis"]["depends_on"]["certs"]["condition"] == "service_completed_successfully"


@pytest.mark.parametrize("service", ["api", "worker"])
def test_the_application_waits_for_its_storage_account(
    services: dict[str, Any], service: str
) -> None:
    condition = services[service]["depends_on"]["minio-init"]["condition"]

    assert condition == "service_completed_successfully"


def test_nothing_depends_on_an_opt_in_service(services: dict[str, Any]) -> None:
    dependencies = {
        dependency for service in services.values() for dependency in service.get("depends_on", {})
    }

    assert not dependencies & set(OPT_IN)


@pytest.mark.parametrize("service", OPT_IN)
def test_an_opt_in_service_is_behind_its_own_profile(
    services: dict[str, Any], service: str
) -> None:
    assert services[service]["profiles"] == [service]


# ---------------------------------------------------------------------------
# The edge — checklist items 1 and 9
# ---------------------------------------------------------------------------

#: Served by FastAPI itself and never part of the product.
_SCHEMA_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


def _allowed() -> list[str]:
    match = re.search(r"^\s*@public\s+path\s+(.+)$", CADDYFILE.read_text("utf-8"), re.MULTILINE)
    assert match, "the Caddyfile's allow-list is one `@public path ...` line"
    return match[1].split()


def _routed(path: str) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in _allowed())


def _api_paths() -> set[str]:
    """Every path the API serves, read from its OpenAPI schema.

    The schema rather than `app.routes`: it is the contract `apps/web` is typed
    from (ADR 0001), it carries `/internal/annotation` too, and FastAPI no
    longer flattens included routers into `app.routes`.
    """
    from tcg_api.app import create_app

    return set(create_app().openapi()["paths"])


def test_every_public_api_route_is_routed() -> None:
    """A route the proxy drops is a feature that works locally and 404s on the host."""
    public = {p for p in _api_paths() if not p.startswith("/internal") and p not in _SCHEMA_PATHS}

    assert public
    assert not sorted(path for path in public if not _routed(path))


def test_no_internal_or_schema_route_is_routed() -> None:
    """ADR 0009's topology, and the promise `SECURITY.md` makes about `/internal`."""
    hidden = {p for p in _api_paths() if p.startswith("/internal")} | _SCHEMA_PATHS

    assert any(path.startswith("/internal") for path in hidden)
    assert not sorted(path for path in hidden if _routed(path))


def test_the_proxy_overwrites_x_forwarded_for() -> None:
    """#269 reads the rightmost hop; an appending proxy would hand it the caller's own."""
    text = CADDYFILE.read_text("utf-8")

    assert "header_up X-Forwarded-For {remote_host}" in text
    assert "trusted_proxies" not in text


def test_everything_else_is_the_web_application() -> None:
    assert "reverse_proxy web:3000" in CADDYFILE.read_text("utf-8")
