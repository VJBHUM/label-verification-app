"""Tests for the secure image-intake module."""

import io

import pytest
from PIL import Image

from backend import imaging


def _img(fmt, size=(1200, 800), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, (120, 60, 30)).save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,expected", [
    ("PNG", "image/png"),
    ("JPEG", "image/jpeg"),
    ("TIFF", "image/tiff"),
    ("BMP", "image/bmp"),
    ("WEBP", "image/webp"),
])
def test_sniff_by_magic_bytes(fmt, expected):
    assert imaging._sniff_media_type(_img(fmt)) == expected


def test_heic_sniffed_by_ftyp_brand():
    data = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1"
    assert imaging._sniff_media_type(data) == "image/heic"


def test_normalize_reencodes_to_jpeg():
    out, mt = imaging.normalize_image(_img("TIFF"))
    assert mt == "image/jpeg"
    assert imaging._sniff_media_type(out) == "image/jpeg"


def test_rgba_preserved_as_png():
    _, mt = imaging.normalize_image(_img("PNG", mode="RGBA"))
    assert mt == "image/png"


def test_downscale_caps_long_edge():
    out, _ = imaging.normalize_image(_img("PNG", size=(4000, 3000)))
    w, h = Image.open(io.BytesIO(out)).size
    assert max(w, h) <= imaging.MAX_LONG_EDGE


def test_exif_and_gps_stripped():
    im = Image.new("RGB", (100, 100))
    exif = im.getexif()
    exif[0x0110] = 6  # orientation
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    out, _ = imaging.normalize_image(buf.getvalue())
    assert not Image.open(io.BytesIO(out)).getexif()


def test_garbage_rejected():
    with pytest.raises(imaging.ImageError):
        imaging.normalize_image(b"this is not an image")


def test_empty_rejected():
    with pytest.raises(imaging.ImageError):
        imaging.normalize_image(b"")


def test_oversized_raw_rejected():
    with pytest.raises(imaging.ImageError):
        imaging.normalize_image(b"\xff\xd8\xff" + b"\x00" * (imaging.MAX_RAW_BYTES + 1))
