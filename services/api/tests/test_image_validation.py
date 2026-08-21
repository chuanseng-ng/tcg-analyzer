"""Uploaded bytes are untrusted input — issue #33, spec §55 and §56.

Every claim the upload endpoint makes about safety is a claim about
`tcg_api.analysis.image_validation`, and this file is where they are asserted.
It needs **no database, no object store and no network**, which is the point:
CI's Python job runs it on every push, so the security properties are checked
even in the jobs that have no live service at all. That is the same reason
`test_analysis_jobs.py` can assert the queue's security properties without a
broker.

The images are built here rather than committed as fixtures. A binary blob in
the repository is one nobody can read at review time, and a test asserting that
GPS coordinates are removed is only convincing if the reader can see them being
put in.
"""

from __future__ import annotations

import re
import struct
import time
import zlib
from hashlib import sha256
from io import BytesIO
from typing import Final, get_args

import pytest
from PIL import Image, PngImagePlugin
from PIL.ExifTags import GPS, IFD, Base
from tcg_api.analysis.image_validation import (
    ACCEPTED_MIME_TYPES,
    InvalidImage,
    validate_image,
)
from tcg_api.routers.analyses import UploadSide
from tcg_domain.analysis import V1_SIDES

#: Generous, so that only the test that is about the pixel limit meets it.
GENEROUS_PIXELS: Final = 50_000_000

#: Small enough to build quickly, large enough that JPEG has something to do.
SIZE: Final = (64, 48)


def a_picture() -> Image.Image:
    """An image with structure in it, so a lossy re-encode would be detectable."""
    picture = Image.new("RGB", SIZE, (200, 40, 40))
    for x in range(SIZE[0]):
        for y in range(SIZE[1]):
            picture.putpixel((x, y), ((x * 5) % 256, (y * 11) % 256, (x + y) % 256))
    return picture


def a_jpeg(**save: object) -> bytes:
    buffer = BytesIO()
    a_picture().save(buffer, "JPEG", quality=92, **save)
    return buffer.getvalue()


def a_png(**save: object) -> bytes:
    buffer = BytesIO()
    a_picture().save(buffer, "PNG", **save)
    return buffer.getvalue()


def personal_metadata() -> Image.Exif:
    """EXIF of the kind a phone writes, including the field spec §54 cares most about."""
    exif = Image.Exif()
    exif[Base.Make] = "TestCam"
    exif[Base.Model] = "TestCam One"
    gps = exif.get_ifd(IFD.GPSInfo)
    gps[GPS.GPSLatitudeRef] = "N"
    gps[GPS.GPSLatitude] = (1.0, 17.0, 3.0)
    gps[GPS.GPSLongitudeRef] = "E"
    gps[GPS.GPSLongitude] = (103.0, 51.0, 0.0)
    return exif


def pixels_of(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as image:
        return image.tobytes()


def exif_of(data: bytes) -> dict[int, object]:
    """The EXIF an image carries, read through a handle that gets closed.

    A helper rather than an expression inside the `assert` it is written for:
    `python -O` drops assertions, and an assertion that opens a file is one
    whose side effect disappears with it.
    """
    with Image.open(BytesIO(data)) as image:
        return dict(image.getexif())


# ---------------------------------------------------------------------------
# The type is sniffed, never declared
# ---------------------------------------------------------------------------
def test_a_jpeg_is_accepted_and_reported_as_one() -> None:
    validated = validate_image(a_jpeg(), max_pixels=GENEROUS_PIXELS)

    assert validated.mime_type == "image/jpeg"


def test_a_png_is_accepted_and_reported_as_one() -> None:
    validated = validate_image(a_png(), max_pixels=GENEROUS_PIXELS)

    assert validated.mime_type == "image/png"


def test_the_accepted_set_is_closed() -> None:
    """A format nobody validated is a decoder nobody reviewed."""
    assert ACCEPTED_MIME_TYPES == ("image/jpeg", "image/png")


def test_a_shell_script_is_not_an_image() -> None:
    """The endpoint takes no filename, so `card.jpg` cannot even be claimed.

    What a caller *can* do is send anything at all in the body, which is what
    this is: an executable, uploaded to an endpoint that accepts pictures.
    """
    with pytest.raises(InvalidImage, match="JPEG or PNG"):
        validate_image(b"#!/bin/sh\nrm -rf /\n", max_pixels=GENEROUS_PIXELS)


def test_image_magic_bytes_alone_do_not_make_an_image() -> None:
    """Sniffing has to be a decode attempt, not a prefix comparison."""
    with pytest.raises(InvalidImage, match="JPEG or PNG"):
        validate_image(b"\x89PNG\r\n\x1a\n" + b"not a png" * 8, max_pixels=GENEROUS_PIXELS)


@pytest.mark.parametrize("unaccepted", ["WEBP", "TIFF", "GIF", "BMP"])
def test_other_real_image_formats_are_refused(unaccepted: str) -> None:
    buffer = BytesIO()
    a_picture().save(buffer, unaccepted)

    with pytest.raises(InvalidImage, match="JPEG or PNG"):
        validate_image(buffer.getvalue(), max_pixels=GENEROUS_PIXELS)


def test_an_empty_body_is_refused() -> None:
    with pytest.raises(InvalidImage, match="no data"):
        validate_image(b"", max_pixels=GENEROUS_PIXELS)


# ---------------------------------------------------------------------------
# Decompression bombs — the pixel limit, applied before the decode
# ---------------------------------------------------------------------------
def a_decompression_bomb() -> bytes:
    """A PNG under a hundred bytes that declares an eleven-gigabyte bitmap.

    Hand-built rather than produced by Pillow, because Pillow will not write one
    — which is exactly why a byte-size limit is no defence at all here.
    """

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    header = struct.pack(">IIBBBBB", 60_000, 60_000, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 1024))
        + chunk(b"IEND", b"")
    )


def test_a_decompression_bomb_is_refused() -> None:
    bomb = a_decompression_bomb()

    assert len(bomb) < 1024, "the point is that the file is tiny and the bitmap is not"
    with pytest.raises(InvalidImage, match="pixels"):
        validate_image(bomb, max_pixels=GENEROUS_PIXELS)


def test_a_decompression_bomb_is_refused_without_being_decoded() -> None:
    """From the header. Decoding 3.6 billion pixels first is not a rejection.

    Timed rather than mocked: the assertion is that no bitmap was allocated, and
    the honest evidence for that is that the call returned immediately. Eleven
    gigabytes takes minutes, so a second is an enormous margin.
    """
    started = time.monotonic()

    with pytest.raises(InvalidImage):
        validate_image(a_decompression_bomb(), max_pixels=GENEROUS_PIXELS)

    assert time.monotonic() - started < 1.0


def test_an_ordinary_image_past_the_limit_is_refused() -> None:
    """The limit is a limit, not only a bomb detector."""
    with pytest.raises(InvalidImage, match="pixels"):
        validate_image(a_png(), max_pixels=SIZE[0] * SIZE[1] - 1)


def test_an_image_exactly_at_the_limit_is_accepted() -> None:
    validated = validate_image(a_png(), max_pixels=SIZE[0] * SIZE[1])

    assert validated.mime_type == "image/png"


# ---------------------------------------------------------------------------
# Decodability
# ---------------------------------------------------------------------------
def test_a_truncated_jpeg_is_refused() -> None:
    """A header that parses and data that stops is not a picture.

    `ImageFile.LOAD_TRUNCATED_IMAGES` must stay off for this to hold: with it on
    the file decodes to a grey band, which the condition models would go on to
    measure as if it were a card.
    """
    whole = a_jpeg()

    with pytest.raises(InvalidImage, match="could not be decoded"):
        validate_image(whole[: len(whole) // 2], max_pixels=GENEROUS_PIXELS)


# ---------------------------------------------------------------------------
# Personal metadata does not survive — spec §54
# ---------------------------------------------------------------------------
def test_a_jpegs_gps_coordinates_do_not_survive() -> None:
    carrying = a_jpeg(exif=personal_metadata())
    attached = exif_of(carrying)
    assert attached, "the fixture must carry EXIF"

    validated = validate_image(carrying, max_pixels=GENEROUS_PIXELS)

    with Image.open(BytesIO(validated.data)) as stripped:
        assert dict(stripped.getexif()) == {}
        assert dict(stripped.getexif().get_ifd(IFD.GPSInfo)) == {}


def test_a_jpegs_comment_does_not_survive() -> None:
    carrying = a_jpeg(comment=b"taken at home")

    validated = validate_image(carrying, max_pixels=GENEROUS_PIXELS)

    assert b"taken at home" not in validated.data


def test_a_pngs_exif_and_text_do_not_survive() -> None:
    text = PngImagePlugin.PngInfo()
    text.add_text("Comment", "taken at home")
    carrying = a_png(pnginfo=text, exif=personal_metadata())
    assert b"taken at home" in carrying, "the fixture must carry the text chunk"

    validated = validate_image(carrying, max_pixels=GENEROUS_PIXELS)

    assert b"taken at home" not in validated.data
    with Image.open(BytesIO(validated.data)) as stripped:
        assert dict(stripped.getexif()) == {}


def test_the_jpeg_segments_that_are_kept_are_kept() -> None:
    """Only the segments that carry personal data go.

    APP0 is JFIF's pixel density, APP2 is the ICC profile — and colour fidelity
    is not a detail in a product that grades surfaces. Dropping every APPn
    because EXIF happens to live in one would be throwing away how the image is
    meant to be interpreted.
    """
    profile = b"\x00" * 128
    carrying = a_jpeg(exif=personal_metadata(), icc_profile=profile)

    validated = validate_image(carrying, max_pixels=GENEROUS_PIXELS)

    assert b"\xff\xe0JFIF" in validated.data[:32] or b"JFIF" in validated.data[:32]
    assert b"ICC_PROFILE" in validated.data
    assert b"Exif\x00\x00" not in validated.data


# ---------------------------------------------------------------------------
# The strip is lossless — the reason it is a marker walk and not a re-encode
# ---------------------------------------------------------------------------
def test_stripping_a_jpeg_changes_no_pixel() -> None:
    """A re-encode would degrade exactly the detail the condition models read."""
    carrying = a_jpeg(exif=personal_metadata(), comment=b"taken at home")

    validated = validate_image(carrying, max_pixels=GENEROUS_PIXELS)

    assert len(validated.data) < len(carrying), "the metadata should actually be gone"
    assert pixels_of(validated.data) == pixels_of(carrying)


def test_stripping_a_png_changes_no_pixel() -> None:
    text = PngImagePlugin.PngInfo()
    text.add_text("Comment", "taken at home")
    carrying = a_png(pnginfo=text, exif=personal_metadata())

    validated = validate_image(carrying, max_pixels=GENEROUS_PIXELS)

    assert pixels_of(validated.data) == pixels_of(carrying)


# ---------------------------------------------------------------------------
# The digest
# ---------------------------------------------------------------------------
def test_the_digest_is_over_the_bytes_that_are_stored() -> None:
    """Not over the bytes that arrived — those are not what anyone kept.

    #39's preprocessing cache is keyed on `images.sha256`, and a key naming
    bytes nobody has is a cache nobody can verify.
    """
    validated = validate_image(a_jpeg(exif=personal_metadata()), max_pixels=GENEROUS_PIXELS)

    assert validated.sha256 == sha256(validated.data).hexdigest()


def test_the_digest_is_bare_lowercase_hex() -> None:
    """The form `images.sha256`'s CHECK constraint requires (#31)."""
    validated = validate_image(a_png(), max_pixels=GENEROUS_PIXELS)

    assert re.fullmatch(r"[0-9a-f]{64}", validated.sha256)


# ---------------------------------------------------------------------------
# The endpoint's side vocabulary
# ---------------------------------------------------------------------------
def test_the_upload_accepts_exactly_the_v1_sides() -> None:
    """`images.side` admits all six of spec §11's values; V1 writes two.

    The other four are §52's guided photography, which no V1 pipeline stage can
    process — so the endpoint must not accept an image it would have nowhere to
    send. The literal shadows the vocabulary, so a test keeps the two in step,
    on the precedent `analysis/tables.py` sets for its migration literal.
    """
    assert get_args(UploadSide) == tuple(side.value for side in V1_SIDES)
