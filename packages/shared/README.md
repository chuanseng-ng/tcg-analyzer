# `packages/shared`

Cross-cutting utilities with no domain knowledge: object storage, logging setup,
identifiers, time and hashing helpers.

If something here needs to know what a card or a grade is, it belongs in
`packages/domain` instead.

## `tcg_shared.storage`

The object-storage port and its adapters — see
[ADR 0002](../../docs/adr/0002-object-storage-behind-a-port.md).

| Module | Holds |
| --- | --- |
| `port` | The `ObjectStorage` protocol and `SignedUrl`. No provider concepts. |
| `keys` | `StorageKey`, `generate_key`, `sanitise_filename`. |
| `errors` | `StorageError` and its subclasses. |
| `memory` | `InMemoryObjectStorage`, for tests and for running without MinIO. |
| `s3` | `S3ObjectStorage` — the only module that imports boto3. |

`import tcg_shared.storage` pulls in nothing outside the standard library, so
the port genuinely does not depend on a provider; bind to one deliberately with
`from tcg_shared.storage.s3 import S3ObjectStorage`. `tests/test_storage_purity.py`
enforces the split, and `tests/test_storage_contract.py` runs one suite against
every adapter — that pairing is what makes an adapter swap safe.

Keys are generated server-side. `generate_key` takes no filename argument, so a
client-supplied name cannot reach a storage path (spec §55).

Storage integration tests need MinIO and are marked `object_storage`:

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait
export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000
uv run pytest -m object_storage
```
