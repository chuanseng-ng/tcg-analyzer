"""Rate limiting for the endpoints spec §55 names — issue #98, ADR 0005.

Spec §55 asks for two things to be limited: analysis endpoints and image
uploads. This module is the mechanism both use, and `routers/analyses.py` is
its first caller.

**A throttled request is a 429 outside the spec §66 envelope**, carrying
`Retry-After`. §66 names eight codes and none of them means "you are going too
fast"; adding a ninth would be a specification change, and this is the third
transport-level failure in a row to answer without one — `GET /analyses/{id}`'s
404 and `POST /analyses/{id}/run`'s 409 both sit outside the taxonomy for the
same reason. `Retry-After` (RFC 9110) is what makes the response parseable, and
every HTTP client already understands it. See
`docs/adr/0005-rate-limiting-the-analysis-endpoints.md`.

**The counter is shared, on the Redis the job queue already runs on.** A
per-process counter is a limit that multiplies by the replica count, which is
theatre for a limit meant to protect a shared database. Redis arrived with #35
and `TCG_API_REDIS_URL` is deliberately named for the store rather than for
Celery, so this is the same instance and not a second piece of infrastructure.

**A client is its address, hashed.** `POST /analyses` opens the session, so on
the call that matters most there is no cookie to key on; a cookie-keyed limit
with an address fallback is reset by discarding the cookie, which makes the
fallback the real limit. `X-Forwarded-For` is deliberately not read: a header
anyone can set is a bypass rather than an identity, and trusting one requires
knowing which proxy is in front of this service.

**It fails open.** A limiter whose counter store is unreachable answers by
letting the request through and logging, because the alternative is that a blip
in Redis takes down the endpoints the limiter exists to keep available. An
absent `TCG_API_REDIS_URL` is the same case and is allowed on the same terms as
an absent database: the service starts, and it simply does not limit.
"""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256

import structlog
from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from tcg_api.config import REDIS_URL_ENV_VAR, get_settings

__all__ = [
    "RATE_LIMIT_KEY_PREFIX",
    "analysis_rate_limit",
    "client_key",
    "get_redis",
]

logger = structlog.get_logger(__name__)

#: Namespaces the counters, so a keyspace shared with Celery's own keys stays
#: readable and a future second limit can be told apart from this one.
RATE_LIMIT_KEY_PREFIX = "ratelimit:analyses:"

#: How long a Redis call may take before the limiter gives up on it. Short, and
#: load-bearing: with no timeout at all redis-py waits forever, so a *hung*
#: Redis — as opposed to a refused one — would hang every request on the
#: endpoints this protects, and "fails open" would quietly mean "fails closed,
#: slowly". A quarter of a second is far longer than an INCR against a healthy
#: instance and far shorter than a caller's patience.
_TIMEOUT_SECONDS = 0.25

#: How much of the digest is kept. Enough that two addresses do not collide;
#: short enough that the key is readable in `redis-cli`.
_DIGEST_LENGTH = 16


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    """Return the process-wide Redis client, built on first use.

    Lazy for the reason `jobs.get_celery_app` is lazy: importing this module must
    not require configuration, only using it. `app.lifespan` closes the client
    and clears this cache on shutdown, which also keeps a connection pool from
    outliving the event loop that created it.
    """
    settings = get_settings()
    if settings.redis_url is None:
        raise RuntimeError(
            f"{REDIS_URL_ENV_VAR} is not set. Point it at Redis, e.g. "
            f"{REDIS_URL_ENV_VAR}=redis://:password@localhost:6379/0. See .env.example."
        )
    # Annotated rather than returned directly: `from_url` is untyped in redis-py,
    # so mypy sees `Any` leaving a function that promises a `Redis`.
    client: Redis = Redis.from_url(
        settings.redis_url,
        socket_timeout=_TIMEOUT_SECONDS,
        socket_connect_timeout=_TIMEOUT_SECONDS,
    )
    return client


def client_key(request: Request) -> str:
    """The counter key for whoever sent `request`.

    The address is hashed so the store never holds one in the clear (spec §54).
    That is obfuscation rather than anonymisation — a truncated unsalted digest
    of an address is brute-forcible, and the honest control is that the key
    expires with the window. Salting it per process is not an option: replicas
    would then count into different buckets, which is the failure the shared
    store exists to avoid.

    A request whose peer the server did not report keys to a shared bucket
    rather than raising. That is the wrong answer for exactly one deployment
    shape and a 500 is the wrong answer for all of them.
    """
    host = request.client.host if request.client else "unknown"
    return RATE_LIMIT_KEY_PREFIX + sha256(host.encode()).hexdigest()[:_DIGEST_LENGTH]


async def analysis_rate_limit(request: Request) -> None:
    """Count this request against its client's window, and refuse it past the limit.

    A FastAPI dependency: declare it on a route with
    ``dependencies=[Depends(analysis_rate_limit)]``.

    One fixed window, one bucket for every endpoint that carries this — an
    analysis is four or five requests end to end, so splitting the budget per
    route would be tuning four numbers to express one policy.
    `# ponytail: fixed window, one shared bucket. A sliding window if a client
    doubling its rate across a boundary ever matters, per-route buckets if
    uploads and starts need different budgets.`
    """
    settings = get_settings()
    if settings.redis_url is None:
        return

    key = client_key(request)
    try:
        client = get_redis()
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            # `nx` so a window is not extended by the traffic inside it, which is
            # what a plain EXPIRE on every request would do — a client at the
            # limit would then never see the window end. Needs Redis >= 7.0;
            # Compose runs `redis:8-alpine`.
            pipe.expire(key, settings.rate_limit_window_seconds, nx=True)
            count, _ = await pipe.execute()

        if int(count) <= settings.rate_limit_requests:
            return

        # Only on the throttled path, so the ordinary request stays one round trip.
        retry_after = max(int(await client.ttl(key)), 1)
    except (RedisError, OSError, RuntimeError):
        # Fails open — see the module docstring. Logged at warning because a
        # limiter that is silently not limiting is worth noticing.
        logger.warning("ratelimit.unavailable", path=request.url.path, exc_info=True)
        return

    # The hashed client, never the address, and nothing else about the caller.
    logger.warning(
        "ratelimit.throttled",
        path=request.url.path,
        client=key.removeprefix(RATE_LIMIT_KEY_PREFIX),
        retry_after=retry_after,
    )
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Too many requests. Retry in {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )
