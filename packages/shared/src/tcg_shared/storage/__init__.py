"""Object storage behind a port — see docs/adr/0002-object-storage-behind-a-port.md.

This package deliberately does **not** re-export the S3 adapter. Importing
``tcg_shared.storage`` must pull in nothing outside the standard library, so
that the port genuinely does not depend on a provider; reach for the adapter
explicitly with ``from tcg_shared.storage.s3 import S3ObjectStorage`` when you
mean to bind to one. `tests/test_storage_purity.py` enforces the split.
"""

from __future__ import annotations

from tcg_shared.storage.errors import (
    InvalidStorageKey,
    ObjectNotFound,
    StorageError,
    StorageUnavailable,
)
from tcg_shared.storage.keys import (
    MAX_FILENAME_LENGTH,
    MAX_KEY_LENGTH,
    StorageKey,
    generate_key,
    sanitise_filename,
)
from tcg_shared.storage.memory import InMemoryObjectStorage
from tcg_shared.storage.port import ObjectStorage, SignedUrl

__all__ = [
    "MAX_FILENAME_LENGTH",
    "MAX_KEY_LENGTH",
    "InMemoryObjectStorage",
    "InvalidStorageKey",
    "ObjectNotFound",
    "ObjectStorage",
    "SignedUrl",
    "StorageError",
    "StorageKey",
    "StorageUnavailable",
    "generate_key",
    "sanitise_filename",
]
