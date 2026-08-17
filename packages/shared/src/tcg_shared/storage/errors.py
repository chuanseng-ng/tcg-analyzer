"""The object store's exception hierarchy.

Modelled on :mod:`tcg_domain.errors`, for the same reason: every error raised
through the port derives from :class:`StorageError`, so a caller can catch the
whole storage layer with one clause, and each concrete error also derives from
the closest builtin so a caller need not learn a private hierarchy to handle it.

The hierarchy exists so that **no caller ever sees a provider's exception type**.
`botocore.exceptions.ClientError` escaping the adapter would make the concrete
provider part of the calling contract, and the whole point of the port is that
providers are replaceable (CLAUDE.md, "external providers are replaceable").
Adapters translate; they do not re-raise.
"""

from __future__ import annotations

__all__ = [
    "InvalidStorageKey",
    "ObjectNotFound",
    "StorageError",
    "StorageUnavailable",
]


class StorageError(Exception):
    """Base class for every error raised by the object-storage layer."""


class InvalidStorageKey(StorageError, ValueError):
    """A storage key is malformed, or could address something it must not.

    Raised for an empty, absolute, over-long or non-ASCII key, and for any key
    containing a path-traversal segment. Rejecting bad input is exactly what a
    `ValueError` means.
    """


class ObjectNotFound(StorageError, LookupError):
    """No object is stored under the given key.

    Also a `LookupError`, because that is what a missing key is; a caller
    reaching for one object among many should not have to import this module to
    handle the miss.
    """


class StorageUnavailable(StorageError, ConnectionError):
    """The object store could not be reached, or refused the credentials.

    This is the provider being unreachable or misconfigured — distinct from a
    key that is simply absent. Also a `ConnectionError`, because retry logic
    written against the builtin should apply here unchanged.
    """
