"""Server-generated object keys.

Spec §55 requires that storage paths be generated server-side and that
arbitrary file access be impossible. Two rules deliver that here:

1. :func:`generate_key` **takes no filename argument**. A client-supplied name
   cannot influence a key because there is no parameter through which it could
   arrive. This is deliberately structural: a validation-based defence has to be
   right about every encoding of ``..``, whereas an absent parameter has nothing
   to be right about.
2. :class:`StorageKey` validates on construction, so even a key assembled by
   hand elsewhere in the codebase cannot address a sibling prefix.

:func:`sanitise_filename` exists for the one legitimate use of the original
name — carrying it as *metadata* — and is never consulted when building a key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from tcg_shared.storage.errors import InvalidStorageKey

__all__ = [
    "MAX_FILENAME_LENGTH",
    "MAX_KEY_LENGTH",
    "StorageKey",
    "generate_key",
    "sanitise_filename",
]

#: S3 caps an object key at 1024 bytes. Enforced here so an over-long key is a
#: local `InvalidStorageKey` rather than a provider error on the first write.
MAX_KEY_LENGTH: Final = 1024

#: Filenames are metadata, not identifiers, so there is no reason to keep a long
#: one. 128 characters is generous for something only ever shown to a human.
MAX_FILENAME_LENGTH: Final = 128

_FALLBACK_FILENAME: Final = "unnamed"

# Each segment must begin with an alphanumeric and may then contain only
# `[A-Za-z0-9._-]`, with single slashes between segments. That one expression
# rejects the whole traversal family at once: a leading or trailing slash, an
# empty segment (`//`), `.` and `..` as segments, backslashes, whitespace,
# control characters, percent-encoding (`%` is not in the class) and every
# non-ASCII character.
_KEY_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")

#: A namespace is one lowercase slug — the top-level prefix a key sits under.
#: It may not contain a slash, so a caller cannot widen its own reach.
_NAMESPACE_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_PATH_SEPARATORS: Final = re.compile(r"[/\\]")
# Whitespace becomes a dash and everything else unusable is simply dropped.
# Replacing both with a dash would turn "café.jpg" into "caf-.jpg", inventing
# punctuation the user never typed; dropping whitespace instead would run words
# together. Each rule is the least surprising one for its own case.
_FILENAME_WHITESPACE: Final = re.compile(r"\s+")
_UNSAFE_FILENAME_CHARACTERS: Final = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True, slots=True)
class StorageKey:
    """The location of one object, relative to whatever bucket backs the store.

    The key is bucket-, region- and provider-agnostic by construction: it is the
    part of an address that survives swapping the adapter.

    Args:
        value: The key itself, e.g. ``uploads/2026/08/17/<uuid>``.

    Raises:
        InvalidStorageKey: If the key is empty, over-long, or contains anything
            that could address a prefix other than its own.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidStorageKey(
                f"a storage key must be a string, got {type(self.value).__name__}"
            )
        if not self.value:
            raise InvalidStorageKey("a storage key must not be empty")
        if len(self.value) > MAX_KEY_LENGTH:
            raise InvalidStorageKey(
                f"a storage key must be at most {MAX_KEY_LENGTH} characters, got {len(self.value)}"
            )
        if not _KEY_PATTERN.match(self.value):
            raise InvalidStorageKey(
                f"{self.value!r} is not a valid storage key. Keys are slash-separated "
                f"segments of [A-Za-z0-9._-], with no leading or trailing slash and no "
                f"traversal. Build one with generate_key() rather than by hand."
            )

    def __str__(self) -> str:
        return self.value


def generate_key(namespace: str) -> StorageKey:
    """Return a fresh key under ``namespace``, owing nothing to any client input.

    The date partition is not decoration: spec §54 requires that stored images
    expire, and a ``namespace/YYYY/MM/DD/`` layout makes that sweep a prefix
    scan instead of a full listing of the bucket.

    Args:
        namespace: A lowercase slug naming the kind of object, e.g. ``uploads``.

    Returns:
        A key of the form ``<namespace>/<YYYY>/<MM>/<DD>/<uuid4>``.

    Raises:
        InvalidStorageKey: If ``namespace`` is not a single lowercase slug.
    """
    if not isinstance(namespace, str) or not _NAMESPACE_PATTERN.match(namespace):
        raise InvalidStorageKey(
            f"namespace must be a single lowercase slug such as 'uploads', got {namespace!r}"
        )

    today = datetime.now(UTC)
    return StorageKey(f"{namespace}/{today:%Y/%m/%d}/{uuid4()}")


def sanitise_filename(value: str) -> str:
    """Reduce a client-supplied filename to a harmless label.

    The result is safe to store as metadata and to show back to a user. It is
    **not** safe to treat as a path, and nothing here should tempt a caller to:
    the return value can never contain a separator, so it addresses nothing.

    Args:
        value: Whatever the client sent, including a full path or nothing at all.

    Returns:
        A separator-free, ASCII, length-capped label, or ``"unnamed"`` when the
        input leaves nothing usable behind.
    """
    # Take the last segment under either separator before anything else, so a
    # full path contributes only its final component.
    basename = _PATH_SEPARATORS.split(value)[-1]
    spaced = _FILENAME_WHITESPACE.sub("-", basename)
    collapsed = _UNSAFE_FILENAME_CHARACTERS.sub("", spaced)[:MAX_FILENAME_LENGTH]

    # A name of only dots and dashes carries no information and, worse, reads
    # like a traversal fragment to whoever sees it next.
    if not collapsed.strip(".-"):
        return _FALLBACK_FILENAME
    return collapsed
