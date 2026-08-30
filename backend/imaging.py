"""Secure image intake and normalization.

Every uploaded image passes through :func:`normalize_image` before it is ever
sent onward. This module is the trust boundary for untrusted binary uploads and
does four jobs, in order:

1. **Validate by content, not by claim.** The browser-supplied `Content-Type` is
   attacker-controlled, so we sniff real magic bytes instead.
2. **Defuse decompression bombs.** A small file can decode to gigapixels; Pillow
   is capped and the error is caught.
3. **Strip metadata (privacy).** Phone photos carry EXIF — including GPS
   coordinates. We apply EXIF orientation, then re-encode so no metadata (and no
   location data) leaves the process.
4. **Normalize for latency.** Oversized images are downscaled to a bounded long
   edge. This is the main lever for the sub-5-second target and cuts token cost,
   while staying large enough to read fine print (the shrunk-warning trick).
"""

import io
import os

from PIL import Image, ImageOps

# Guard against decompression bombs: refuse to fully decode anything that would
# expand past this many pixels (~50 MP — comfortably above any real bottle photo).
Image.MAX_IMAGE_PIXELS = int(os.environ.get("LABEL_VERIFIER_MAX_PIXELS", str(50_000_000)))

# Longest edge we keep. High enough to read small warning type, bounded enough to
# stay fast. Tunable per deployment.
MAX_LONG_EDGE = int(os.environ.get("LABEL_VERIFIER_MAX_EDGE", "2200"))

# Largest raw upload we will even attempt to decode (per image), pre-normalization.
MAX_RAW_BYTES = int(os.environ.get("LABEL_VERIFIER_MAX_RAW_BYTES", str(15 * 1024 * 1024)))

JPEG_QUALITY = int(os.environ.get("LABEL_VERIFIER_JPEG_QUALITY", "90"))


class ImageError(ValueError):
    """Raised for any invalid / unsafe / unreadable image. Message is user-safe."""


def _sniff_media_type(data: bytes) -> str | None:
    """Detect image type from magic bytes. Returns a media type or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def normalize_image(data: bytes) -> tuple[bytes, str]:
    """Validate, sanitize, and normalize an uploaded image.

    Returns ``(normalized_bytes, media_type)`` where media_type is always
    ``image/jpeg`` or ``image/png``. Raises :class:`ImageError` on anything
    invalid or unsafe — the message is safe to show a user.
    """
    if not data:
        raise ImageError("The uploaded file is empty.")
    if len(data) > MAX_RAW_BYTES:
        raise ImageError(
            f"Image is too large (max {MAX_RAW_BYTES // (1024 * 1024)} MB). "
            "Please use a smaller photo."
        )
    if _sniff_media_type(data) is None:
        raise ImageError("Unsupported or unreadable file. Please upload a PNG, JPEG, WEBP, or GIF image.")

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()  # force full decode inside the guard so bombs are caught here
            # Apply EXIF orientation, then drop all metadata on re-encode (removes GPS/PII).
            img = ImageOps.exif_transpose(img)

            has_alpha = "A" in img.getbands()

            width, height = img.size
            if max(width, height) > MAX_LONG_EDGE:
                scale = MAX_LONG_EDGE / max(width, height)
                img = img.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)

            out = io.BytesIO()
            if has_alpha:
                # Preserve transparency (label artwork PNGs) without EXIF.
                img.convert("RGBA").save(out, format="PNG", optimize=True)
                return out.getvalue(), "image/png"
            img.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Image.DecompressionBombError:
        raise ImageError("Image dimensions are too large to process safely.")
    except ImageError:
        raise
    except Exception:
        # Corrupt file, truncated upload, unsupported internal format, etc.
        raise ImageError("The file could not be read as a valid image.")
