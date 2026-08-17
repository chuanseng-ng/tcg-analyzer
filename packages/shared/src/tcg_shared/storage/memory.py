"""An in-memory object store.

Two uses, both real. It is the reference implementation the contract tests run
alongside the S3 adapter — that pairing is what makes "swapping the adapter
requires no change to calling code" a demonstrated property rather than a claim
— and it lets a developer or a test exercise code that stores images without
running MinIO.

What it cannot do is honour a signature: the URLs it mints record the grant but
nothing enforces it, because there is no server to enforce it. Only a real
S3-compatible store can prove expiry, so the tests that check enforcement run
against MinIO alone. Never use this adapter where the signature has to hold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from tcg_shared.storage.errors import ObjectNotFound
from tcg_shared.storage.keys import StorageKey
from tcg_shared.storage.port import SignedUrl

__all__ = ["InMemoryObjectStorage"]

#: Scheme of the URLs this adapter mints. Deliberately not http(s): nothing can
#: dereference one, so an accidental use in production fails loudly rather than
#: silently serving an unsigned object.
_SCHEME = "memory"


@dataclass(frozen=True, slots=True)
class _StoredObject:
    data: bytes
    content_type: str


@dataclass(slots=True)
class InMemoryObjectStorage:
    """An object store backed by a dictionary.

    Args:
        objects: The initial contents, if any. Supplied mostly so a test can
            arrange a store without awaiting a series of puts.
    """

    objects: dict[StorageKey, _StoredObject] = field(default_factory=dict)

    async def put(self, key: StorageKey, data: bytes, *, content_type: str) -> None:
        self.objects[key] = _StoredObject(data=data, content_type=content_type)

    async def get(self, key: StorageKey) -> bytes:
        try:
            return self.objects[key].data
        except KeyError:
            raise ObjectNotFound(f"no object is stored under {key}") from None

    async def delete(self, key: StorageKey) -> None:
        # Absent is the outcome deletion wants; it already holds.
        self.objects.pop(key, None)

    async def signed_upload_url(
        self,
        key: StorageKey,
        *,
        content_type: str,
        expires_in: timedelta,
    ) -> SignedUrl:
        # The content type goes into the URL because S3 signs it too: an upload
        # sending a different one is rejected there. Recording it keeps the two
        # adapters describing the same grant, even though nothing enforces it.
        return self._grant("put", key, expires_in, content_type=content_type)

    async def signed_download_url(self, key: StorageKey, *, expires_in: timedelta) -> SignedUrl:
        return self._grant("get", key, expires_in)

    @staticmethod
    def _grant(
        operation: str,
        key: StorageKey,
        expires_in: timedelta,
        *,
        content_type: str | None = None,
    ) -> SignedUrl:
        expires_at = datetime.now(UTC) + expires_in
        granted = f"operation={operation}&expires={int(expires_at.timestamp())}"
        if content_type is not None:
            granted += f"&content_type={quote(content_type)}"
        return SignedUrl(url=f"{_SCHEME}://{key}?{granted}", expires_at=expires_at)
