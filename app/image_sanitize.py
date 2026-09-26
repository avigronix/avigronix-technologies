"""Re-encode uploaded images so no metadata survives.

Phone photos carry EXIF (camera model, timestamps and often GPS
coordinates) plus XMP/ICC/text chunks. Shop images are served publicly, so
every upload is decoded and re-encoded from its pixels only. Decoding also
proves the file really is an image of the type its extension claims.
"""
import io

from PIL import Image, ImageOps, UnidentifiedImageError

# A 5 MB upload can still decode to an enormous bitmap ("decompression
# bomb"). 40 megapixels is far above any logo/banner/QR need.
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

FORMAT_FOR_EXTENSION = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


class InvalidImageError(ValueError):
    """The data isn't a decodable image (or is too large when decoded)."""


def strip_metadata(data: bytes, extension: str) -> bytes:
    """Return `data` re-encoded in the format matching `extension`, with all
    metadata removed. Raises InvalidImageError for anything that isn't a
    real image."""
    target = FORMAT_FOR_EXTENSION.get(extension.lower())
    if target is None:
        raise InvalidImageError(f"unsupported extension {extension!r}")
    try:
        with Image.open(io.BytesIO(data)) as img:
            # Header only so far — reject oversized bitmaps before decoding.
            if img.width * img.height > MAX_PIXELS:
                raise InvalidImageError("image dimensions too large")
            img.load()
            transparency = img.info.get("transparency")
            # Apply the EXIF orientation to the pixels before the tag is
            # dropped, so rotated phone photos stay upright.
            clean = ImageOps.exif_transpose(img).copy()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as e:
        raise InvalidImageError(str(e)) from e

    # Nothing from the original header is carried over (EXIF, XMP, ICC,
    # PNG text chunks, comments) — only transparency, which is image data.
    clean.info = {"transparency": transparency} if transparency is not None and target == "PNG" else {}

    if target == "JPEG" and clean.mode not in ("RGB", "L"):
        clean = clean.convert("RGB")
    if target == "WEBP" and clean.mode not in ("RGB", "RGBA"):
        clean = clean.convert("RGBA" if "A" in clean.getbands() or transparency is not None else "RGB")

    out = io.BytesIO()
    if target == "JPEG":
        clean.save(out, "JPEG", quality=95, optimize=True)
    elif target == "PNG":
        clean.save(out, "PNG", optimize=True)
    else:
        clean.save(out, "WEBP", quality=95, method=4)
    return out.getvalue()


# Keys Pillow puts in `img.info` that describe how pixels are encoded rather
# than who/where/when — everything else counts as metadata.
_ENCODING_INFO_KEYS = {
    "dpi", "gamma", "transparency", "interlace", "aspect", "jfif", "jfif_version", "jfif_unit",
    "jfif_density", "progressive", "progression", "loop", "duration", "background", "srgb",
    "chromaticity", "lossless", "adobe", "adobe_transform",
}


def metadata_keys(data: bytes) -> list:
    """Names of the metadata blocks present in an image ([] if none).
    Raises InvalidImageError if it isn't a readable image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            found = sorted(k for k in img.info if k not in _ENCODING_INFO_KEYS)
            if img.getexif() and "exif" not in found:
                found.append("exif")
            return found
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as e:
        raise InvalidImageError(str(e)) from e
