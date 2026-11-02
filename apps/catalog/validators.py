"""Upload checks for product media. Extension alone is never trusted: content is sniffed too."""

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

IMAGE_EXTENSIONS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".gif": "GIF"}
VIDEO_EXTENSIONS = {".mp4", ".webm"}
# SVG is deliberately excluded: it can carry scripts.


def media_type_for(filename: str) -> str:
    return "video" if Path(filename).suffix.lower() in VIDEO_EXTENSIONS else "image"


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.0f} MB"


def validate_media_file(file) -> None:
    if getattr(file, "_committed", False):
        return  # already stored and validated on upload; don't re-read it on every save
    ext = Path(file.name).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        _check_image(file, IMAGE_EXTENSIONS[ext])
    elif ext in VIDEO_EXTENSIONS:
        _check_video(file, ext)
    else:
        allowed = ", ".join(sorted([*IMAGE_EXTENSIONS, *VIDEO_EXTENSIONS]))
        raise ValidationError(f"Unsupported file type. Allowed: {allowed}.")


def _check_image(file, expected_format: str) -> None:
    limit = settings.CATALOG_MAX_IMAGE_BYTES
    if file.size > limit:
        raise ValidationError(f"Image is too large (max {_mb(limit)}).")
    try:
        file.seek(0)
        with Image.open(file) as img:
            actual = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValidationError("The file is not a valid image.") from None
    finally:
        file.seek(0)
    allowed_formats = {"JPEG", "PNG", "WEBP", "GIF"}
    if actual not in allowed_formats:
        raise ValidationError("Unsupported image format.")


def _check_video(file, ext: str) -> None:
    limit = settings.CATALOG_MAX_VIDEO_BYTES
    if file.size > limit:
        raise ValidationError(f"Video is too large (max {_mb(limit)}).")
    file.seek(0)
    head = file.read(12)
    file.seek(0)
    is_mp4 = ext == ".mp4" and head[4:8] == b"ftyp"
    is_webm = ext == ".webm" and head[:4] == b"\x1a\x45\xdf\xa3"
    if not (is_mp4 or is_webm):
        raise ValidationError("The file is not a valid video.")
