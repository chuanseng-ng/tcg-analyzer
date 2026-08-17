"""The object-storage port.

Spec §8 asks for "S3-compatible object storage" and spec §55 for signed URLs.
This module says what the application may ask of a store; it says nothing about
who provides it. There is deliberately no `bucket`, `region`, `endpoint` or
`credential` anywhere below — those are an adapter's private business, and a
port that leaked them would make the provider part of the calling contract.
That is CLAUDE.md's "external providers are replaceable" invariant, expressed
as a type.

The five operations are exactly the ones the product needs and no more. `list`
and `exists` are absent because nothing needs them yet; retention sweeps
(spec §54) will say what they need when M2 builds them.

Every method is `async` because the API service is async throughout and a
blocking call on the event loop is an outage under load. An adapter over a
synchronous client is expected to offload to a worker thread rather than force
the port to be synchronous.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from tcg_shared.storage.keys import StorageKey

__all__ = ["ObjectStorage", "SignedUrl"]


@dataclass(frozen=True, slots=True)
class SignedUrl:
    """A time-limited URL granting one operation on one object.

    Args:
        url: The signed URL. Treat it as a credential: anyone holding it has
            whatever access it was minted for, until it expires.
        expires_at: When the signature stops being honoured, timezone-aware.
    """

    url: str
    expires_at: datetime


class ObjectStorage(Protocol):
    """Somewhere bytes can be stored under a server-generated key.

    Implementations must raise only :mod:`tcg_shared.storage.errors` types, so
    swapping one for another changes no caller's error handling.
    """

    async def put(self, key: StorageKey, data: bytes, *, content_type: str) -> None:
        """Store ``data`` under ``key``, replacing anything already there.

        Args:
            key: Where to store it. Build this with
                :func:`tcg_shared.storage.keys.generate_key`.
            data: The bytes to store.
            content_type: The MIME type to record alongside them.

        Raises:
            StorageUnavailable: If the store could not be reached.
        """
        ...

    async def get(self, key: StorageKey) -> bytes:
        """Return the bytes stored under ``key``.

        Raises:
            ObjectNotFound: If nothing is stored under ``key``.
            StorageUnavailable: If the store could not be reached.
        """
        ...

    async def delete(self, key: StorageKey) -> None:
        """Remove the object at ``key``.

        Deleting a key that holds nothing succeeds. Deletion is used for
        retention (spec §54), where the outcome that matters is "it is gone" —
        and it already was.

        Raises:
            StorageUnavailable: If the store could not be reached.
        """
        ...

    async def signed_upload_url(
        self,
        key: StorageKey,
        *,
        content_type: str,
        expires_in: timedelta,
    ) -> SignedUrl:
        """Mint a URL a client may use to upload one object, once.

        The key is chosen by the server, so a client holding this URL can write
        to that one location and nowhere else (spec §55).

        Raises:
            StorageUnavailable: If the store could not be reached.
        """
        ...

    async def signed_download_url(self, key: StorageKey, *, expires_in: timedelta) -> SignedUrl:
        """Mint a URL a client may use to read one object.

        Scoped to a single key so that possession of one URL never implies
        access to another object (spec §55, "prevent arbitrary file access").

        Raises:
            StorageUnavailable: If the store could not be reached.
        """
        ...
