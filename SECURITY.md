# Security policy

## Reporting a vulnerability

Report it privately, through GitHub:
[**Security → Report a vulnerability**](https://github.com/chuanseng-ng/tcg-analyzer/security/advisories/new).
That opens a draft advisory only you and the maintainer can read.

**Please do not open a public issue for a vulnerability.** This repository is
public and takes photograph uploads; a report in the issue tracker is a
disclosure.

There is no e-mail address here on purpose — the advisory form is the one path,
so a report cannot be lost in a mailbox.

## Supported version

`main`, and only `main`. Nothing is released or deployed, there are no tagged
versions, and there is nothing to back-port a fix to.

## In scope

- The public API: `/analyses/*`, `/cards/*`, `/grading-companies`,
  `/health`, `/readiness`. [`docs/api.md`](docs/api.md) says what each endpoint
  refuses and why.
- The upload path — `POST /analyses/{id}/images` — and the worker that decodes
  the photographs it stores. Uploaded images are untrusted input; the worker
  runs OpenCV over them.
- The web application (`apps/web`).
- Anything in the repository that handles a credential, a session cookie, or an
  object-storage key.

## Not in scope, with one exception

`/internal/*` and the annotation tool (`apps/annotation`) are **not public
surfaces**. They carry no authentication by design — they are kept off the
public origin by deployment topology rather than by a second service. See
[ADR 0009](docs/adr/0009-the-dataset-store-as-a-database-domain.md).

The exception: **a report that either one is reachable from the public origin is
a deployment finding, and is in scope for that reason.** That is exactly the
failure the design depends on not happening.

Also out of scope: findings against the local development stack
(`infrastructure/local/`), which publishes datastore ports and uses the
credentials in `.env.example` on purpose, and anything that needs an attacker to
already control the host.

## What is already known

[`docs/security-review.md`](docs/security-review.md) is a dated reading of spec
§55 and §56 against the tree: one verdict per requirement — met, not met, or
decided unnecessary — with the evidence, and the issue that closes each gap. If
what you found is listed there as *not met* or as a recorded ceiling, it is
already on the record; a report saying it is worse than the review claims is
still worth sending.

## What to expect

An acknowledgement, a fix on `main`, and credit in the advisory if you want it.

There is **no bounty**, no CVE process, and no security mailing list.
