"""Deciding whether uploaded bytes are an image, and making them safe to keep.

Issue #33, spec §55 and §56. **Uploaded images are untrusted input** — this is
the project's primary attack surface — so this module is the deliverable rather
than a wrapper around one. It is deliberately pure: bytes in, bytes out, with no
database, no object store and no FastAPI anywhere in it. That is what lets CI's
Python job assert every security property below without a live service, the same
reason `test_analysis_jobs.py` can assert the queue's properties without a
broker.

Four rules, and why each is the shape it is:

* **The type is sniffed, never declared.** :func:`validate_image` is not given
  the request's ``Content-Type`` and has no parameter through which one could
  arrive. A file named ``card.jpg`` announcing ``image/jpeg`` proves nothing;
  what is recorded on the row is what Pillow read out of the magic bytes.
* **The pixel count is checked from the header, before anything is decoded.**
  A byte-size limit alone is not a decompression-bomb defence: a two-kilobyte
  PNG can declare 60000 by 60000 and cost eleven gigabytes to decode. The
  dimensions are read from the header, compared, and only then is ``load()``
  called — by which point the decode is bounded.
* **Decodability is proved, not assumed.** A file whose header parses and whose
  data is truncated is refused here rather than three pipeline stages later.
  ``ImageFile.LOAD_TRUNCATED_IMAGES`` is left at its default for exactly that
  reason; turning it on would make a truncated upload succeed and produce a
  grey band the condition models would happily measure.
* **Personal metadata is removed losslessly.** Spec §54 notes that these
  photographs may contain hands, backgrounds and personal surroundings; EXIF
  GPS is worse, because it is the one field that is precise. The strip does
  *not* re-encode: a JPEG is rebuilt from its own marker segments with the
  entropy-coded scan copied byte for byte, and a PNG is re-saved through a
  format that is lossless by definition. Re-compressing would degrade exactly
  the fine surface and edge signal the condition models exist to read.

**The digest is over the bytes that are stored**, not the bytes that arrived.
#39's preprocessing cache is keyed on `images.sha256`, and a key naming bytes
nobody kept is a cache that cannot be verified; #41 has to be able to check what
it deleted.

**Only JPEG and PNG.** Phone cameras produce the first and desktop exports the
second. HEIC, WebP and TIFF are refused — an accepted format is one whose
metadata this module knows how to remove, and a format nobody validated is a
decoder nobody reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Final

from PIL import Image, UnidentifiedImageError

__all__ = [
    "ACCEPTED_MIME_TYPES",
    "InvalidImage",
    "ValidatedImage",
    "validate_image",
]

#: What an accepted upload is recorded as in `images.mime_type`. Keyed by
#: Pillow's own format name, so the value written to the row is derived from
#: what was read out of the file rather than from what the client said.
_MIME_TYPES: Final[dict[str, str]] = {"JPEG": "image/jpeg", "PNG": "image/png"}

#: The closed set, for the message a rejection carries and for tests.
ACCEPTED_MIME_TYPES: Final = tuple(sorted(_MIME_TYPES.values()))

# The four user-facing messages. Fixed strings: the acceptance criterion is that
# every failure maps to `invalid_image` "with no internal detail leaked", so the
# decoder's own exception goes to the log and never into a response body.
_EMPTY: Final = "The upload contains no data."
_NOT_AN_IMAGE: Final = "The upload is not a JPEG or PNG image."
_NOT_DECODABLE: Final = "The image could not be decoded."


class InvalidImage(ValueError):
    """The upload is not an image this service will accept.

    A `ValueError` because that is what rejecting malformed input is. The
    message is written to be shown to a user: it says which rule was broken and
    nothing about how the decoder failed.
    """


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    """An upload that passed, and the three facts a row is written from.

    Args:
        data: The bytes to store — stripped of personal metadata, and *not* the
            bytes that arrived.
        mime_type: The sniffed type, one of :data:`ACCEPTED_MIME_TYPES`.
        sha256: A digest over ``data``, as 64 lowercase hex characters, which is
            the form `images.sha256`'s CHECK constraint requires.
    """

    data: bytes
    mime_type: str
    sha256: str


def validate_image(data: bytes, *, max_pixels: int) -> ValidatedImage:
    """Accept `data` as a storable image, or say why not.

    Args:
        data: Whatever arrived in the request body. Already bounded in length by
            the caller — the byte limit is applied while streaming, so that an
            oversized upload is refused without ever being held in full.
        max_pixels: The largest decoded bitmap this service will produce, in
            pixels. Separate from the byte limit on purpose; see the module
            docstring.

    Raises:
        InvalidImage: If the upload is empty, is not a JPEG or a PNG, declares
            more than ``max_pixels`` pixels, or cannot be decoded.
    """
    if not data:
        raise InvalidImage(_EMPTY)

    try:
        image = Image.open(BytesIO(data))
    except UnidentifiedImageError as error:
        raise InvalidImage(_NOT_AN_IMAGE) from error
    except Image.DecompressionBombError as error:
        # Pillow's own ceiling, tripped at `open` for anything vast enough that
        # even reading the header is a refusal. Reported as the pixel-limit
        # rejection rather than as a decode failure, because that is what it is.
        raise InvalidImage(_too_many_pixels(max_pixels)) from error
    except (OSError, ValueError) as error:
        # A header that begins like an image and then stops making sense.
        raise InvalidImage(_NOT_AN_IMAGE) from error

    with image:
        mime_type = _MIME_TYPES.get(image.format or "")
        if mime_type is None:
            raise InvalidImage(_NOT_AN_IMAGE)

        # Before `load()`, and that ordering is the whole defence.
        if image.width * image.height > max_pixels:
            raise InvalidImage(_too_many_pixels(max_pixels))

        try:
            image.load()
        except (OSError, ValueError) as error:
            raise InvalidImage(_NOT_DECODABLE) from error

        stripped = _strip_jpeg(data) if image.format == "JPEG" else _repack_png(image)

    return ValidatedImage(
        data=stripped,
        mime_type=mime_type,
        sha256=sha256(stripped).hexdigest(),
    )


def _too_many_pixels(max_pixels: int) -> str:
    return f"The image is larger than {max_pixels:,} pixels."


# ---------------------------------------------------------------------------
# Removing personal metadata, without touching a pixel.
# ---------------------------------------------------------------------------

_SOI: Final = b"\xff\xd8"

#: Markers carrying no length word, so the walker steps over them by two bytes.
#: `D0` to `D7` are the restart markers, `01` is TEM, `D8`/`D9` frame the file.
_STANDALONE: Final = frozenset({0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)})

#: The segments that are dropped, and everything they can hold:
#: APP1 is EXIF — including GPS — and XMP; APP13 is the Photoshop image
#: resource block, which carries IPTC location; COM is a free-text comment.
#: Everything else is kept deliberately. APP0 is JFIF's density; APP2 is the ICC
#: profile, and colour fidelity matters to a product that grades surfaces; APP14
#: is Adobe's colour-transform flag, whose absence inverts a CMYK JPEG.
_DROPPED: Final = frozenset({0xE1, 0xED, 0xFE})

_SOS: Final = 0xDA


def _strip_jpeg(data: bytes) -> bytes:
    """Rebuild a JPEG without its metadata segments, pixel-identical to the input.

    The scan is not touched: on reaching SOS the remainder of the file is copied
    verbatim, so what is stored decodes to exactly the bitmap that arrived. That
    is the point of doing this rather than re-saving through Pillow, which would
    re-compress a photograph whose fine detail is the measurement.

    `# ponytail: a marker-level walk, not a JPEG parser. It removes the segments
    metadata actually lives in and copies everything from the first SOS onward
    untouched — so a trailer appended after EOI, or an APP segment placed
    between scans of a progressive image, survives. Replace this with a real
    parser only if a camera is found that puts anything personal there.`
    """
    if not data.startswith(_SOI):
        raise InvalidImage(_NOT_DECODABLE)

    out = bytearray(_SOI)
    position = 2
    end_of_data = len(data)

    while position < end_of_data:
        if data[position] != 0xFF:
            raise InvalidImage(_NOT_DECODABLE)

        # Any number of 0xFF fill bytes may precede a marker. They are dropped
        # rather than copied: the rebuilt file emits one 0xFF per marker.
        marker_at = position + 1
        while marker_at < end_of_data and data[marker_at] == 0xFF:
            marker_at += 1
        if marker_at >= end_of_data:
            raise InvalidImage(_NOT_DECODABLE)

        marker = data[marker_at]
        if marker in _STANDALONE:
            out += bytes((0xFF, marker))
            position = marker_at + 1
            continue

        if marker_at + 3 > end_of_data:
            raise InvalidImage(_NOT_DECODABLE)
        # The length word counts itself, so it is never less than two.
        length = int.from_bytes(data[marker_at + 1 : marker_at + 3], "big")
        segment_end = marker_at + 1 + length
        if length < 2 or segment_end > end_of_data:
            raise InvalidImage(_NOT_DECODABLE)

        if marker == _SOS:
            out += bytes((0xFF, marker)) + data[marker_at + 1 : segment_end]
            out += data[segment_end:]
            return bytes(out)

        if marker not in _DROPPED:
            out += bytes((0xFF, marker)) + data[marker_at + 1 : segment_end]
        position = segment_end

    # No scan: the file described an image and never contained one.
    raise InvalidImage(_NOT_DECODABLE)


def _repack_png(image: Image.Image) -> bytes:
    """Re-save a PNG, which is lossless, keeping only what is not personal.

    Pillow writes text chunks only from an explicit ``pnginfo`` argument, so
    `tEXt`, `iTXt` and `zTXt` are gone by not being asked for. `eXIf` is popped
    from ``info`` rather than merely not passed, because which of ``info`` and
    the encoder arguments that key is read from has moved between Pillow
    releases and this must not depend on the answer. The ICC profile and the
    transparency index survive, deliberately: neither says anything about a
    person, and both change how the image is interpreted.
    """
    image.info.pop("exif", None)
    image.info.pop("XML:com.adobe.xmp", None)

    packed = BytesIO()
    image.save(packed, "PNG")
    return packed.getvalue()
