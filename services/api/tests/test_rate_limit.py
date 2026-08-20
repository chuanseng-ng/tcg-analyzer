"""The rate limiter — #98, spec §55.

No Redis and no database anywhere in this file, deliberately. What has to be
right here is a set of decisions — that a throttled request is a 429 outside the
spec §66 envelope, that an unconfigured limiter does not break the service, and
that a limiter whose store is down fails *open* — and every one of them is a
branch rather than a round trip. That is what lets CI's plain Python job assert
them, on `test_analysis_jobs.py`'s precedent.

The stub below is not a Redis emulator and does not try to be. It implements the
three commands this module issues, which makes these tests a check of the
limiter's logic rather than of Redis's. The claim that Redis behaves as assumed
is asserted where it can be: against the real stack, in CI's `compose` job.
"""

from __future__ import annotations

from typing import Any, Self

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from tcg_api import rate_limit
from tcg_api.config import get_settings
from tcg_api.rate_limit import analysis_rate_limit, client_key
from tcg_api.routers import analyses, cards

LIMIT = 3
WINDOW = 60


class FakePipeline:
    """The two commands the limiter pipelines, queued and then executed."""

    def __init__(self, store: FakeRedis) -> None:
        self._store = store
        self._queued: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self._queued.append(("incr", (key,)))

    def expire(self, key: str, seconds: int, nx: bool = False) -> None:
        self._queued.append(("expire", (key, seconds, nx)))

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for command, args in self._queued:
            if command == "incr":
                self._store.counts[args[0]] = self._store.counts.get(args[0], 0) + 1
                results.append(self._store.counts[args[0]])
            else:
                key, seconds, nx = args
                if not nx or key not in self._store.ttls:
                    self._store.ttls[key] = seconds
                results.append(True)
        return results


class FakeRedis:
    """An in-memory stand-in for the three commands `rate_limit` issues."""

    def __init__(self, *, fails: bool = False) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.fails = fails

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        if self.fails:
            raise ConnectionError("Redis is down.")
        return FakePipeline(self)

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)


@pytest.fixture
def limited(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """A configured limiter of `LIMIT` requests per `WINDOW`, over a fake store."""
    monkeypatch.setenv("TCG_API_REDIS_URL", "redis://:secret@localhost:6379/0")
    monkeypatch.setenv("TCG_API_RATE_LIMIT_REQUESTS", str(LIMIT))
    monkeypatch.setenv("TCG_API_RATE_LIMIT_WINDOW_SECONDS", str(WINDOW))
    get_settings.cache_clear()

    store = FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: store)
    return store


@pytest.fixture
def client() -> TestClient:
    """A minimal application carrying only the dependency under test.

    Deliberately not `create_app()`: the real analysis endpoints need PostgreSQL,
    and what is being asserted is the limiter, not what it guards.
    """
    app = FastAPI()

    @app.post("/guarded", dependencies=[Depends(analysis_rate_limit)])
    async def guarded() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


# ---------------------------------------------------------------------------
# The limit itself
# ---------------------------------------------------------------------------
def test_a_client_under_the_limit_is_never_throttled(
    client: TestClient,
    limited: FakeRedis,
) -> None:
    """#98's acceptance criterion, and the half that matters most in practice."""
    for _ in range(LIMIT):
        assert client.post("/guarded").status_code == 200


def test_the_request_past_the_limit_is_throttled(
    client: TestClient,
    limited: FakeRedis,
) -> None:
    for _ in range(LIMIT):
        client.post("/guarded")

    assert client.post("/guarded").status_code == 429


def test_the_limit_comes_from_configuration(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    limited: FakeRedis,
) -> None:
    """Raising the setting raises the limit, so it is a knob rather than a constant."""
    monkeypatch.setenv("TCG_API_RATE_LIMIT_REQUESTS", str(LIMIT + 1))
    get_settings.cache_clear()

    for _ in range(LIMIT + 1):
        assert client.post("/guarded").status_code == 200
    assert client.post("/guarded").status_code == 429


def test_a_throttled_response_says_when_to_retry(
    client: TestClient,
    limited: FakeRedis,
) -> None:
    """`Retry-After` is what makes a 429 parseable without a ninth error code."""
    for _ in range(LIMIT):
        client.post("/guarded")

    response = client.post("/guarded")

    assert response.headers["Retry-After"] == str(WINDOW)


def test_a_throttled_response_is_not_an_error_envelope(
    client: TestClient,
    limited: FakeRedis,
) -> None:
    """ADR 0005: 429 sits outside spec §66, as the 404 and the 409 already do.

    A `code` field here would mean the taxonomy had quietly grown a ninth member
    without the specification changing.
    """
    for _ in range(LIMIT):
        client.post("/guarded")

    body = client.post("/guarded").json()

    assert "code" not in body
    assert body["detail"]


def test_the_error_taxonomy_is_still_eight_codes() -> None:
    """The decision, stated where a future limiter change would trip over it."""
    from tcg_api.errors import ErrorCode

    assert len(ErrorCode) == 8
    assert not any("rate" in code.value for code in ErrorCode)


# ---------------------------------------------------------------------------
# Degrading — an unconfigured limiter, and one whose store is down
# ---------------------------------------------------------------------------
def test_an_unconfigured_limiter_does_not_limit(
    client: TestClient,
    unconfigured_environment: None,
) -> None:
    """Absent `TCG_API_REDIS_URL` is allowed, exactly as it is for the queue."""
    for _ in range(LIMIT + 5):
        assert client.post("/guarded").status_code == 200


def test_a_limiter_whose_store_is_down_fails_open(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    limited: FakeRedis,
) -> None:
    """ADR 0005: an unreachable counter store must not take the endpoint down."""
    monkeypatch.setattr(rate_limit, "get_redis", lambda: FakeRedis(fails=True))

    for _ in range(LIMIT + 5):
        assert client.post("/guarded").status_code == 200


def test_a_limiter_that_cannot_build_a_client_fails_open(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    limited: FakeRedis,
) -> None:
    """A malformed or unusable URL is the same case: warn, and let the call through."""

    def unbuildable() -> object:
        raise RuntimeError("TCG_API_REDIS_URL is not set.")

    monkeypatch.setattr(rate_limit, "get_redis", unbuildable)

    assert client.post("/guarded").status_code == 200


# ---------------------------------------------------------------------------
# Who a client is
# ---------------------------------------------------------------------------
def test_the_key_holds_no_address(client: TestClient, limited: FakeRedis) -> None:
    """Spec §54: the store learns that *someone* called, never who.

    Obfuscation rather than anonymisation — see ADR 0005 — but a keyspace dump
    should not be a list of addresses.
    """
    client.post("/guarded")

    (key,) = limited.counts
    assert "testclient" not in key
    assert key.startswith(rate_limit.RATE_LIMIT_KEY_PREFIX)


def test_two_addresses_get_two_buckets() -> None:
    """Otherwise one busy client throttles everybody else."""
    first = client_key(_request_from("198.51.100.7"))
    second = client_key(_request_from("203.0.113.9"))

    assert first != second
    assert client_key(_request_from("198.51.100.7")) == first


def test_a_request_with_no_client_still_keys() -> None:
    """An ASGI server that reports no peer must not 500 the endpoint."""
    assert client_key(_request_from(None)).startswith(rate_limit.RATE_LIMIT_KEY_PREFIX)


def _request_from(host: str | None) -> Any:
    """The smallest thing `client_key` reads: a request with a client address."""
    from starlette.requests import Request

    scope: dict[str, Any] = {"type": "http", "headers": []}
    if host is not None:
        scope["client"] = (host, 51234)
    return Request(scope)


# ---------------------------------------------------------------------------
# Wiring — which endpoints carry the dependency
# ---------------------------------------------------------------------------
def test_the_analysis_writes_are_limited_and_the_poll_is_not() -> None:
    """Spec §55 names the analysis endpoints; #98 reads that as the writes.

    `GET /analyses/{id}` is the endpoint spec §65 requires a client to poll, so
    limiting it would throttle the product's own progress reporting.
    """
    limited = {
        (route.path, method)
        for route in analyses.router.routes
        for method in route.methods
        if any(
            dependency.call is analysis_rate_limit for dependency in route.dependant.dependencies
        )
    }

    assert ("/analyses", "POST") in limited
    assert ("/analyses/{analysis_id}/run", "POST") in limited
    assert ("/analyses/{analysis_id}", "GET") not in limited


def test_the_catalog_reads_are_not_limited() -> None:
    """Spec §55 does not name them, and `/cards/search` is the web app's search box."""
    for route in cards.router.routes:
        assert all(
            dependency.call is not analysis_rate_limit
            for dependency in route.dependant.dependencies
        ), route.path
