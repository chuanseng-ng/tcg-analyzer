# ADR 0005 — Rate limiting the analysis endpoints

- **Status:** accepted
- **Date:** 2026-08-20
- **Refs:** M2, #98, spec §54, §55, §65, §66

## Context

Spec §55 requires that analysis endpoints and image uploads be rate-limited.
Nothing in the repository limited anything: #28 bundled a limiter with
`GET /cards/search` and split it out again on the grounds that a catalog search
is neither of the two things §55 names, and #32 and #35 both shipped their
endpoints unlimited and pointed here. Two questions had to be answered before
any limiter could be written, and each had a real alternative.

**What is the body of a 429?** `tcg_api/errors.py` transcribes spec §66's eight
codes and states that adding a ninth is a specification change rather than a
local decision; `test_openapi.py` asserts the set exactly. None of the eight
means "you are going too fast" — `provider_error` is the nearest and describes a
call that failed, not a caller being throttled. So either §66 grows a
`rate_limited` code, with the spec, the enum, the taxonomy test and
`apps/web/lib/api-types.ts` all following, or a 429 is answered outside the
envelope and becomes a failure clients cannot parse the way they parse the rest.

**Per-process or shared?** An in-memory counter is one line of configuration and
multiplies by the replica count, which for a limit protecting a shared database
is theatre wherever more than one process serves traffic. A shared counter needs
a store, and a store is a service in Compose, a variable in `.env.example` and a
row in `tests/test_compose_stack.py`'s `EXPECTED_SERVICES`.

Two smaller questions came with them. **Which endpoints** — §55 names two
targets and neither is a catalog read, so whether `GET /cards/search` and
`GET /cards/{id}` are also limited was a judgement rather than a requirement.
And **what identifies a client** in a product with no accounts (spec §53), where
the only durable handle on a user is the anonymous session cookie that
`POST /analyses` itself issues.

## Decision

**A throttled request is `429 Too Many Requests` with a `Retry-After` header,
outside the spec §66 envelope. §66 stays closed at eight codes.** The body is
FastAPI's own `{"detail": ...}`. This is the third transport-level failure to be
answered this way rather than the first: `GET /analyses/{id}`'s 404 and
`POST /analyses/{id}/run`'s 409 both sit outside the taxonomy, on the reasoning
that `errors.py` deliberately leaves transport-level failures alone and that
none of the eight codes describes them. Growing the taxonomy for a 429 while its
sibling failures stayed outside would make the envelope less predictable, not
more. `Retry-After` (RFC 9110) carries the one piece of information a client
acts on, and every HTTP client already understands it.

**The counter is shared, in the Redis the job queue already runs on.** #35 put
Redis into Compose and named its setting `TCG_API_REDIS_URL` — for the store,
not for Celery — precisely so this issue would not propose a second one. No
Compose change was needed.

**Only the endpoints spec §55 names are limited**: `POST /analyses` and
`POST /analyses/{id}/run` today, and #33's upload on arrival.
`GET /analyses/{id}` is deliberately not limited — spec §65 requires a client to
poll it, so throttling it would throttle the product's own progress reporting.
The catalog reads stay unlimited: they are cheap reads of a public catalog, and
`/cards/search` is the web application's search box.

**A client is its address, hashed**: `sha256(request.client.host)`, truncated,
under a `ratelimit:analyses:` prefix. `POST /analyses` is the call that issues
the session cookie, so on the endpoint that creates rows there is no session to
key on; a cookie-keyed limit with an address fallback is reset by discarding the
cookie, which makes the fallback the real limit. `X-Forwarded-For` is not read:
a header any client can set is a bypass rather than an identity.

**The limiter fails open.** An unreachable Redis, an unusable URL, or an unset
`TCG_API_REDIS_URL` means the request is served and a warning is logged. The
mechanism is a fixed-window `INCR` plus `EXPIRE ... NX`, in one pipelined round
trip; `TTL` is read only on the throttled path.

## Consequences

A client that exceeds the limit gets a response it can act on without knowing
anything about this project's error taxonomy, and the taxonomy remains a closed
set of eight product failures rather than a grab-bag that grows whenever a new
status code appears. The cost is that `apps/web` has two error shapes to handle
on the analysis endpoints — the §66 envelope for 503s, and a bare `detail` for
404, 409 and now 429. That cost was already paid by #32 and #35; this decision
does not add to it, but it does entrench it.

Sharing Redis means one limit across replicas and one piece of infrastructure
for two consumers — and it means an outage in Redis is an outage in both.
Failing open bounds that: a Redis blip degrades to unlimited rather than to
unavailable. The other direction of that trade is real and deliberate — while
Redis is down there is no limit at all, and an attacker who can take Redis down
has removed the limiter with it. The limiter protects an available service from
a heavy client; it is not a defence against an adversary who already has that
reach, and nothing here should be read as one.

Keying on an address means clients behind one NAT share a bucket, which is why
the limit is configurable and generous. Hashing keeps addresses out of the store
in the clear, but a truncated unsalted digest of an address is brute-forcible:
it is obfuscation, and the control that actually bounds retention is that the
key expires with the window. Salting per process was rejected because replicas
would then count into different buckets, which is the failure the shared store
exists to avoid.

A fixed window lets a client send up to twice the limit across a boundary. That
ceiling is named in `tcg_api/rate_limit.py` with its upgrade path — a sliding
window — and is a fair trade for a mechanism that is one round trip and thirty
lines. Per-route buckets were not built: an analysis is four or five requests
end to end, so one budget expresses the policy where four numbers would only be
four numbers to tune.

## Addendum — 2026-09-07 (#269)

The decision above stands for a deployment with nothing in front of the API.
Two things about it are out of date, and this addendum records both rather than
rewriting a decision that was correct on the day it was taken.

**The limited routes are five, not three.** The Decision section names
`POST /analyses` and `POST /analyses/{id}/run` "today, and #33's upload on
arrival". The upload arrived, and two more writes joined it as the milestones
landed: `POST /analyses/{id}/confirm-card` (#104) and
`POST /analyses/{id}/economic-configuration` (#65). All five carry
`Depends(analysis_rate_limit)` and share the one bucket, which is still the
policy — an analysis is four or five requests end to end.
`test_rate_limit.py` and `test_openapi.py` assert all five since #269; they
asserted two and three before, which is how the list drifted unnoticed.

**`X-Forwarded-For` is read when, and only when, a setting says how.** The
refusal above — *"a header any client can set is a bypass rather than an
identity, and trusting one requires knowing which proxy is in front of this
service"* — is right about the risk and right about what is missing. #273's
production overlay supplies what is missing by terminating TLS in a proxy this
project operates; without a way to say so, `request.client.host` is that
proxy's address and every beta user shares one bucket of thirty requests a
minute. The determination, made by #263's security review and implemented here:

- **`TCG_API_TRUSTED_PROXY_COUNT`** (`Settings.trusted_proxy_count`), default
  `0`. At `0` the header is not read at all and the limiter is exactly the one
  above; local development and CI run at `0`.
- At *n*, the client is the address **n hops from the right** of
  `X-Forwarded-For` — the one the *n*-th trusted proxy appended. Never the
  leftmost entry: everything to the left of the deployment's own hops is what
  the caller chose to send, and reading it restores the bypass. Setting the
  count higher than the number of proxies actually operated is that same
  mistake spelled as configuration.
- A request carrying **fewer than *n* hops** did not come through those proxies
  and keys on `request.client.host`, as before.
- The overlay's proxy **overwrites** the header rather than appending to a
  client-supplied one (#273), which makes the junk to the left disappear
  entirely; counting from the right is what keeps the limiter correct if it
  ever appends instead.
- One header and one count. No `Forwarded` (RFC 7239) and no `X-Real-IP`: a
  second syntax to parse is a second way to be wrong about the same question.

`client_key` in `tcg_api/rate_limit.py` remains the single place this is
decided. The window, the limit, the shared bucket, the fail-open rule, the
hashing and the 429's shape are all unchanged.

## Addendum — 2026-09-08 (#270)

**The limited endpoints are eight, and one of them is a read.**

Spec §68's feedback loop added three: `POST /analyses/{id}/feedback`, which
mints a return code, and `GET /feedback/{code}` / `POST /feedback/{code}`,
which are the way back to it weeks later. All three carry
`Depends(analysis_rate_limit)` and share the one bucket, which is still the
policy — the Consequences above argue that per-route buckets would be four
numbers expressing one decision, and eight would be worse.

**The GET is the first read this service limits, and that is not a drift.** The
Decision above limits writes because a read is cheap and `GET /analyses/{id}` is
the endpoint spec §65 requires a client to poll. `GET /feedback/{code}` is
different in kind: its path parameter is a **bearer capability**, so the request
is an assertion of authority rather than a question about an identifier the
caller already holds. A hundred bits of entropy is what makes one guess
worthless; this limit is what stops a client making many. Neither is sufficient
alone, which is why the route carries the dependency rather than relying on the
code's length. `GET /analyses/{id}` stays unlimited: a session cookie is a
bearer capability too, but polling it is the product's own progress reporting.

`test_rate_limit.py` now asserts the set of limited routes as an **equality**
rather than by membership. The list drifted twice before anybody noticed (#263
found it at three-versus-five), and membership assertions are what let it: a
ninth limited route now has to be written down rather than merely working.

Nothing else changes. The window, the limit, the shared bucket, the fail-open
rule, the hashing, the 429's shape and `client_key` are all as the addendum
above left them.

## Addendum — 2026-09-10 (#148)

**The limited endpoints are ten, and the second bearer capability is a delete.**

ADR 0008's approved training-image class 4 added three routes.
`POST /analyses/{id}/training-consent` is a write against an analysis and is
limited for the Decision's original reason. `DELETE /training-consent/{code}`
is limited for the addendum above's: its path parameter is a **bearer
capability**, so the request asserts an authority rather than naming an
identifier the caller already holds, and entropy alone bounds what one guess is
worth while the limiter bounds how many guesses there are.

**`GET /training-consent` is not limited**, and the distinction is the one the
Decision draws. It serves the consent text and its version — the same words for
everybody, carrying nothing about anybody and reachable before any session
exists. Limiting it would throttle the question rather than the answer, and a
user who cannot read what they are agreeing to cannot consent to it.

All three share the one bucket, which is still the policy for the reason the
Consequences give. `test_rate_limit.py`'s equality now names ten, and it also
asserts that `GET /training-consent` is *not* among them — the negative half
matters here, because the obvious mistake is to limit the whole router.

Nothing else changes. The window, the limit, the shared bucket, the fail-open
rule, the hashing, the 429's shape and `client_key` are all as the two addenda
above left them.
