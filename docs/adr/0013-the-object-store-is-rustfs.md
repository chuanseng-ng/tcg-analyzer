# ADR 0013 — The object store in every Compose file is RustFS

- **Status:** accepted
- **Date:** 2026-09-27
- **Refs:** M0, M10, #273, spec §8, §55, §56, ADR 0002, ADR 0003, ADR 0012

## Context

Spec §8 asks for "S3-compatible object storage" and names no provider. ADR 0002
put the store behind the `ObjectStorage` port in `packages/shared`, and ADR 0003
chose MinIO to stand behind it locally: the same protocol and signature
algorithm a deployment uses, in a container rather than a billing account. ADR
0012's production overlay kept MinIO and added an `mc admin` job that scopes the
application's account to its one bucket.

MinIO Inc. withdrew the community edition during 2026. Binary downloads stopped
in October 2025, the repository was archived in April 2026, the Docker Hub
repository was deleted on 2026-09-11 (PR #316 moved the pin to quay.io), and by
2026-09-24 quay.io refused anonymous pulls of `minio/minio` and `minio/mc`. The
last free release also carries CVE-2026-40344, an authentication bypass in the
Snowball handler. Every CI job that starts the stack failed from 2026-09-21,
first on the scheduled run of `main` and then on every pull request, and there
was no newer tag to bump to. A store this project can pull is the constraint.

What the application needs from the store is small. `tcg_shared.storage.s3` is
the only S3 caller: `put_object`, `get_object`, `delete_object`, a paginated
`list_objects_v2`, and presigned GET and PUT URLs, over path-style S3v4. The
Compose files additionally need a healthcheck, a way to create the bucket before
the API starts, and, in production, a user with a JSON policy limiting it to the
bucket.

The options:

- **Build MinIO from its archived source.** Keeps every script. An AGPL fork
  nobody patches, carrying a known authentication bypass, with binaries this
  project would have to host itself. Rejected.
- **RustFS** (`rustfs/rustfs`, Apache-2.0, 1.0.0 released 2026-09-16). Modelled
  on MinIO: root credentials by environment, an S3 API on 9000 and a console on
  9001, an AWS-format IAM policy language, a `/health` endpoint, and an
  `mc`-compatible client `rustfs/rc` with `mb`, `admin user add`,
  `admin policy create` and `admin policy attach`. A young project; the
  measured behaviour below is what this ADR rests on, not its age.
- **SeaweedFS** (Apache-2.0). Mature and widely pulled. Identities and per-bucket
  actions come from a JSON configuration file rather than an admin API, and
  there is no object console. Workable; a larger rewrite of the init job.
- **Versity S3 gateway** (Apache-2.0). Simplest to run, but its access control
  is per-user roles rather than a JSON policy, so the production overlay's
  "one bucket, four actions" scope would be coarser than it is today.

## Decision

Both Compose files run **RustFS**, pinned by digest, under a service named for
its role rather than its vendor: `storage`, with volume `storage-data`. The
bucket is created by a one-shot `storage-init` service on the `rustfs/rc`
client image, which `api` and `worker` depend on with
`service_completed_successfully`, the pattern `migrate` already uses. The
production overlay's `storage-init` also creates the application's user and
attaches the same one-bucket policy as before.

The Compose variables are `STORAGE_ROOT_USER`, `STORAGE_ROOT_PASSWORD`,
`STORAGE_PORT`, `STORAGE_CONSOLE_PORT`, `STORAGE_APP_ACCESS_KEY` and
`STORAGE_APP_SECRET_KEY`. Nothing is called MinIO any more, including the
service, so the next vendor change is a one-line image edit.

Measured on 2026-09-27 against `rustfs/rustfs:1.0.0` and `rustfs/rc:v0.1.36`,
and the reason the decision is safe to make now:

- `/health` answers 200 within a second of start; the image runs as uid 10001
  and owns `/data`, so a fresh named volume needs no `user:` or `chown`.
- `rc mb --ignore-existing`, `rc admin user add`, `rc admin policy create` and
  `rc admin policy attach` all exit 0 on a second run, so `up` twice is safe.
- The scoped account's `ListBuckets` returns only its bucket; listing another
  bucket and creating one are `AccessDenied`; a missing key is `NoSuchKey`, so
  `/readiness` reads 404 and not 403.
- `put`, `get`, `list_objects_v2`, `delete`, and presigned GET and PUT all
  behave as they did on MinIO, and `packages/shared/tests/test_storage_contract.py`
  passes unchanged against it.

`tcg_shared.storage.s3` is untouched. ADR 0002's port did its job.

## Consequences

- **The stack starts again**, locally and in CI, and the production overlay's
  scoping checklist item (#273, item 7) still holds and is still asserted by CI.
- **A one-shot init container is now on the local path too.** MinIO let a
  `mkdir` under its data root stand in for bucket creation; RustFS keeps bucket
  metadata of its own, so the bucket is made through the API. "Just the backing
  services" is now two commands, `up -d --wait postgres storage` and then
  `run --rm storage-init`, because `--wait` fails on a requested service that
  has exited, even at 0, unless something else in the request depends on it.
- **Existing local volumes are orphaned.** `minio-data` is not migrated; a
  developer runs `down -v` and starts from an empty bucket, as the local README
  already tells them a `down -v` does. Production has no deployment yet (ADR
  0012 left the host open), so there is nothing to migrate there.
- **A young dependency.** RustFS 1.0.0 is eleven days old at the time of
  writing. Dependabot's `docker_compose` ecosystem tracks the tag, and the
  digest pin means an upstream change is a reviewed bump rather than a surprise.
  The behaviours above are the acceptance test for any bump.
- **Historical documents keep saying MinIO.** ADRs 0002, 0003 and 0012 and the
  security review are records of their dates and are not rewritten; this ADR is
  the pointer from them.
