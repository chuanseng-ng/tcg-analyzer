# ADR 0002 — Object storage behind a port

- **Status:** accepted
- **Date:** 2026-08-17
- **Refs:** M0, #17, spec §8, §54, §55

## Context

Spec §8 asks for "S3-compatible object storage" and names no provider. Spec §55
turns that into requirements the application must meet however the storage is
supplied: signed object-storage URLs, server-generated storage paths, sanitised
filenames, and no arbitrary file access. Spec §54 adds that stored images must
not be retained indefinitely.

CLAUDE.md's standing invariant is that external providers are replaceable and
that none may become a hard dependency of the core. Object storage is the first
provider seam the codebase actually reaches, so the shape chosen here is the one
`MarketDataProvider` and `GradingCompanyAdapter` will copy.

Three questions had no obvious answer.

**Where the code lives.** A `packages/object-storage` would read well, but spec
§7 fixes the contents of `packages/` to five entries and
`tests/test_repository_structure.py` is the in-repo record of that list. A new
directory would mean editing the transcription of the spec in order to add a
feature the spec already anticipated.

**Synchronous or asynchronous client.** The API is async throughout and a
blocking call on the event loop is an outage under load, so the port must be
async. That does not settle the client: `aioboto3` is natively async but pins a
narrow `botocore` range and trails the reference implementation that every
S3-compatible provider tests against.

**How a filename is kept away from a path.** Traversal defences are a family of
bugs, not one: `..`, `..\`, `%2e%2e`, and whatever encoding is discovered next.

## Decision

```text
packages/shared/src/tcg_shared/storage/
  port.py    ObjectStorage protocol, SignedUrl      stdlib only
  keys.py    StorageKey, generate_key               stdlib only
  errors.py  StorageError hierarchy                 stdlib only
  memory.py  InMemoryObjectStorage                  stdlib only
  s3.py      S3ObjectStorage                        boto3
```

- The port lives in **`packages/shared`**, which spec §7 already provides for
  cross-cutting concerns with no domain knowledge.
- **`import tcg_shared.storage` pulls in nothing outside the standard library.**
  Only `tcg_shared.storage.s3` binds to boto3, and `tests/test_storage_purity.py`
  asserts both halves by importing each in a fresh interpreter.
- The port exposes five operations — put, get, delete, signed upload URL, signed
  download URL — and no `bucket`, `region`, `endpoint` or credential. Those are
  an adapter's private business.
- **`generate_key` takes no filename argument.** Keys are
  `namespace/YYYY/MM/DD/uuid4`, generated server-side. `sanitise_filename` is a
  separate function for keeping an original name as metadata, and is never
  consulted when building a key. The date partition makes a §54 retention sweep
  a prefix scan rather than a listing of the whole bucket.
- The adapter uses **synchronous boto3 offloaded with `anyio.to_thread.run_sync`**.
  URL signing is an HMAC over a canonical request string, so it runs inline; only
  the two operations that touch the network pay for a thread.
- Adapters raise only `tcg_shared.storage.errors` types. A botocore exception
  never escapes.
- **MinIO locally**, in the same Compose file as PostgreSQL, with a one-shot
  container that creates the bucket.
- Credentials reach the API as `TCG_API_STORAGE_*` settings, with the secret key
  typed `SecretStr` — the first use of it in this codebase.

## Consequences

- An adapter can be swapped without touching a caller, and this is demonstrated
  rather than asserted: one contract suite in `tests/test_storage_contract.py`
  runs against both adapters. Adding a third store means adding a fixture
  parameter. If it cannot pass, it is not an `ObjectStorage`.
- Traversal is not defended against, it is structurally impossible: there is no
  parameter through which a client-supplied name could reach a key. Every
  encoding of `..` is therefore irrelevant rather than handled.
- **`packages/shared` now carries a provider dependency.** That is the real cost
  of not creating a new package, and the purity test is what stops it spreading:
  boto3 is reachable from exactly one module, and the test fails the moment the
  port acquires it.
- `services/api` gains the repository's first workspace-internal dependency,
  with an explicit `[tool.uv.sources]` entry. `uv sync --all-packages` would
  have hidden its absence locally; the API image resolves only declared
  dependencies and would not have.
- Signature enforcement can only be tested against a real implementation, so
  those tests need MinIO. They carry an `object_storage` marker, separate from
  `integration`, and run in their own CI job driving the local Compose file —
  which makes that file a tested artifact on every PR.
- The threadpool hop costs a context switch per storage operation. Measured
  against the alternative — a narrower `botocore` pin and a less-exercised code
  path against non-AWS providers — that is the cheaper risk. If the hop ever
  shows up in a latency budget, only `s3.py` changes.
- Retention (§54), MIME and size validation, and rate limiting are all still
  open. This ADR delivers the abstraction they will be built on, not the
  policies themselves; those belong to M2.
- A presigned URL is generated against whatever endpoint the API is configured
  with. Once #20 runs the API inside Compose, that is `http://minio:9000`, which
  a browser on the host cannot reach. M2 will need a separate public-endpoint
  setting; adding one now would be speculative.
