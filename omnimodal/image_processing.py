from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from PIL import Image, ImageOps

from omnimodal.config import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_MAX_DIMENSION,
    env_int,
    extreme_aspect_ratio_limit,
    image_format,
)
from omnimodal.errors import ReadImageError, tr

_IMAGE_FORMAT_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "WEBP": "image/webp",
    "TIFF": "image/tiff",
    "ICO": "image/x-icon",
}
_LOSSLESS_FORMATS = {"PNG", "GIF", "BMP", "WEBP", "TIFF", "ICO"}
EXTREME_SLICE_MIN_DIMENSION = 256
EXTREME_SLICE_OVERLAP = 16
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _mime_for_format(image_format_name: str) -> str:
    return _IMAGE_FORMAT_MIME.get(image_format_name, "image/jpeg")


def _has_transparency(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _save_image(
    image: Image.Image,
    image_format_name: str,
    quality: int,
) -> bytes:
    buffer = io.BytesIO()
    try:
        if image_format_name == "JPEG":
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
        elif image_format_name == "PNG":
            image.save(buffer, format="PNG", optimize=True)
        elif image_format_name == "GIF":
            image.save(buffer, format="GIF", optimize=True)
        elif image_format_name == "WEBP":
            image.save(buffer, format="WEBP", lossless=True)
        elif image_format_name == "TIFF":
            image.save(buffer, format="TIFF", compression="tiff_lzw")
        else:
            image.save(buffer, format=image_format_name)
    except OSError as exc:
        raise ReadImageError(
            tr(
                f"图片压缩失败：{exc}",
                f"Image compression failed: {exc}",
            )
        ) from exc
    return buffer.getvalue()


def _prepare_lossless_image(
    image: Image.Image,
    image_format_name: str,
) -> Image.Image:
    prepared = image
    if image_format_name == "GIF":
        if prepared.mode in {"RGBA", "LA", "PA"}:
            prepared = prepared.quantize(colors=256)
        elif prepared.mode not in {"P", "L", "RGB"}:
            prepared = prepared.convert("RGB")
    elif prepared.mode in {
        "CMYK",
        "YCbCr",
        "LAB",
        "HSV",
        "I;16",
        "I;16B",
        "I;16L",
    }:
        prepared = prepared.convert("RGBA" if _has_transparency(prepared) else "RGB")
    elif image_format_name == "BMP" and prepared.mode == "P":
        prepared = prepared.convert("RGBA" if _has_transparency(prepared) else "RGB")
    return prepared


def _resize_image(image: Image.Image, max_dimension: int) -> Image.Image:
    if max(image.size) <= max_dimension:
        return image
    scale = max_dimension / max(image.size)
    new_size = (
        max(1, round(image.size[0] * scale)),
        max(1, round(image.size[1] * scale)),
    )
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _resize_slice(image: Image.Image, max_dimension: int) -> Image.Image:
    scale = max_dimension / max(image.size)
    new_size = (
        max(1, round(image.size[0] * scale)),
        max(1, round(image.size[1] * scale)),
    )
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _decode_image_bytes(
    data: bytes,
    label: str,
) -> tuple[Image.Image, str, bool]:
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            original_format = opened.format or "JPEG"
            animated = getattr(opened, "n_frames", 1) > 1
            image = ImageOps.exif_transpose(opened).copy()
    except Exception as exc:
        raise ReadImageError(
            tr(
                f"图片无法解码，请确认图片数据有效：{label} ({exc})",
                f"Image could not be decoded; confirm the image data is valid: {label} ({exc})",
            )
        ) from exc
    return image, original_format, animated


def _data_url_bytes(raw: str) -> bytes | None:
    if not raw.lower().startswith("data:"):
        return None
    try:
        header, payload = raw.split(",", 1)
    except ValueError as exc:
        raise ReadImageError(
            tr(
                "data URL 格式无效。",
                "Invalid data URL format.",
            )
        ) from exc
    if "base64" not in header.lower():
        raise ReadImageError(
            tr(
                "data URL 必须是 base64 编码。",
                "Data URL must use base64 encoding.",
            )
        )
    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, TypeError) as exc:
        raise ReadImageError(
            tr(
                "data URL 的 base64 数据无效。",
                "Data URL contains invalid base64 data.",
            )
        ) from exc


def _bare_base64_bytes(raw: str) -> bytes | None:
    if len(raw) < 64 or not _BASE64_RE.match(raw):
        return None
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        return None
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
    except Exception:
        return None
    return data


def _read_image_source(raw: str) -> tuple[Image.Image, str, bool]:
    raw = raw.strip()
    if not raw:
        raise ReadImageError(tr("image 参数为空。", "image argument is empty."))

    data_url = _data_url_bytes(raw)
    if data_url is not None:
        return _decode_image_bytes(data_url, "data URL")

    bare_base64 = _bare_base64_bytes(raw)
    if bare_base64 is not None:
        return _decode_image_bytes(bare_base64, "base64")

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise ReadImageError(tr(f"找不到图片文件：{path}", f"Image file not found: {path}"))
    try:
        data = path.read_bytes()
    except PermissionError as exc:
        raise ReadImageError(
            tr(
                f"图片文件被占用或无权限访问，请等待写入完成、复制到稳定路径或重试：{path}",
                f"Image file is locked or not accessible. Wait for the write to finish, "
                f"copy it to a stable path, or retry: {path}",
            )
        ) from exc
    except OSError as exc:
        raise ReadImageError(
            tr(
                f"图片文件读取失败：{path} ({exc})",
                f"Image file could not be read: {path} ({exc})",
            )
        ) from exc
    try:
        return _decode_image_bytes(data, str(path))
    except PermissionError as exc:
        raise ReadImageError(
            tr(
                f"图片文件被占用或无权限访问，请等待写入完成、复制到稳定路径或重试：{path}",
                f"Image file is locked or not accessible. Wait for the write to finish, "
                f"copy it to a stable path, or retry: {path}",
            )
        ) from exc


def _slice_extreme_image(image: Image.Image) -> list[Image.Image]:
    limit = extreme_aspect_ratio_limit()
    width, height = image.size
    long_side = max(width, height)
    short_side = min(width, height)
    if short_side <= 0 or long_side / short_side <= limit:
        return [image]

    max_dimension = max(
        1,
        env_int("READ_IMAGE_MAX_DIMENSION", DEFAULT_MAX_DIMENSION),
    )
    segment_len = max(
        1,
        round(max_dimension * short_side / EXTREME_SLICE_MIN_DIMENSION),
    )
    overlap = min(EXTREME_SLICE_OVERLAP, max(1, segment_len // 10))
    step = max(1, segment_len - overlap)

    slices: list[Image.Image] = []
    if width > height:
        starts = list(range(0, width, step))
        if starts[-1] + segment_len < width:
            starts.append(width - segment_len)
        for x in starts:
            segment = image.crop((x, 0, min(x + segment_len, width), height))
            slices.append(_resize_slice(segment, max_dimension))
    else:
        starts = list(range(0, height, step))
        if starts[-1] + segment_len < height:
            starts.append(height - segment_len)
        for y in starts:
            segment = image.crop((0, y, width, min(y + segment_len, height)))
            slices.append(_resize_slice(segment, max_dimension))
    return slices


def _prepare_image_from_pil(
    image: Image.Image,
    original_format: str,
    animated: bool,
) -> tuple[bytes, str]:
    max_dimension = max(
        1,
        env_int("READ_IMAGE_MAX_DIMENSION", DEFAULT_MAX_DIMENSION),
    )
    image = _resize_image(image, max_dimension)
    quality = min(
        100,
        max(1, env_int("READ_IMAGE_JPEG_QUALITY", DEFAULT_JPEG_QUALITY)),
    )

    if image_format() != "jpeg" and original_format.upper() in _LOSSLESS_FORMATS:
        image_format_name = original_format.upper()
        if image_format_name == "GIF" and animated:
            image_format_name = "PNG"
        prepared = _prepare_lossless_image(image, image_format_name)
        return (
            _save_image(prepared, image_format_name, quality),
            _mime_for_format(image_format_name),
        )

    image = _to_rgb(image)
    return _save_image(image, "JPEG", quality), "image/jpeg"


def prepare_image_variants(raw: str) -> list[tuple[bytes, str]]:
    image, original_format, animated = _read_image_source(raw)
    return [
        _prepare_image_from_pil(segment, original_format, animated)
        for segment in _slice_extreme_image(image)
    ]


def prepare_image(raw: str) -> tuple[bytes, str]:
    return prepare_image_variants(raw)[0]
