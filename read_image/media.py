from __future__ import annotations

import base64
import io
import mimetypes
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageOps

from read_image import api
from read_image.config import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_MAX_DIMENSION,
    DEFAULT_VIDEO_MAX_MB,
    env_int,
    image_format,
    video_base64_max_bytes,
    video_download_max_bytes,
    video_keep_audio,
)
from read_image.errors import (
    ReadImageError,
    VisionMediaError,
    is_media_error,
    normalize_error_code,
    tr,
)
from read_image.urls import validate_remote_url

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
MAX_VIDEO_CONVERSION_DEPTH = 2
VIDEO_CRF_720 = 28
VIDEO_CRF_480 = 32
VIDEO_SCALE_720 = 720
VIDEO_SCALE_480 = 480
VIDEO_TRANSCODE_TIMEOUT_SEC = 600
VIDEO_DOWNLOAD_TIMEOUT_SEC = 120


def _active_provider():
    return api.default_client.provider


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


def prepare_image(raw_path: str) -> tuple[bytes, str]:
    raw_path = raw_path.strip()
    if not raw_path:
        raise ReadImageError(tr("image 参数为空。", "image argument is empty."))

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise ReadImageError(tr(f"找不到图片文件：{path}", f"Image file not found: {path}"))

    try:
        with Image.open(path) as opened:
            opened.load()
            original_format = opened.format or "JPEG"
            animated = getattr(opened, "n_frames", 1) > 1
            image = ImageOps.exif_transpose(opened).copy()
    except Exception as exc:
        raise ReadImageError(
            tr(
                f"图片无法解码，请确认文件是支持的图片格式：{path} ({exc})",
                f"Image could not be decoded; confirm it is a supported format: {path} ({exc})",
            )
        ) from exc

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


def _ffmpeg_executable() -> str:
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise ReadImageError(
            tr(
                f"视频处理依赖 FFmpeg 不可用：{exc}",
                f"FFmpeg dependency unavailable: {exc}",
            )
        ) from exc


def _video_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "video/mp4"


def _file_data_url(path: Path) -> str:
    mime = _video_mime(path)
    data = path.read_bytes()
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _transcode_video(
    input_path: Path,
    output_path: Path,
    *,
    scale: int | None = None,
    crf: int = VIDEO_CRF_720,
) -> None:
    ffmpeg = _ffmpeg_executable()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
    ]
    if video_keep_audio():
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if scale is not None:
        cmd += ["-vf", f"scale=-2:{scale}"]
    cmd.append(str(output_path))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=VIDEO_TRANSCODE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReadImageError(
            tr(
                "视频转换超时，请稍后重试或先压缩视频。",
                "Video conversion timed out; retry later or compress the video first.",
            )
        ) from exc
    if result.returncode != 0 or not output_path.is_file():
        detail = result.stderr.strip()[-500:] if result.stderr else "(无输出)"
        raise ReadImageError(tr(f"视频转换失败：{detail}", f"Video conversion failed: {detail}"))


def _remote_size(url: str) -> int | None:
    try:
        response = api.http_client.head(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        if response.status_code >= 400:
            return None
        raw = response.headers.get("Content-Length")
        return int(raw) if raw else None
    except Exception:
        return None


def _video_too_large_error(max_bytes: int) -> ReadImageError:
    return ReadImageError(
        tr(
            "视频文件较大，不支持上传。请先自行压缩或裁剪到 "
            f"{max_bytes // (1024 * 1024)}MB 以内，再重试。",
            f"Video file is too large to upload. Compress or trim it below "
            f"{max_bytes // (1024 * 1024)}MB and retry.",
        )
    )


def _download_video_url(
    url: str,
    destination: Path,
    max_bytes: int | None = None,
) -> None:
    max_bytes = max_bytes or video_download_max_bytes()
    part = destination.with_name(f"{destination.name}.part")
    try:
        with api.http_client.stream(
            "GET",
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=VIDEO_DOWNLOAD_TIMEOUT_SEC,
        ) as response:
            if response.status_code >= 400:
                response.read()
                raise ReadImageError(
                    tr(
                        f"下载视频失败（HTTP {response.status_code}）",
                        f"Video download failed (HTTP {response.status_code})",
                    )
                )
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    advertised = int(content_length)
                except ValueError:
                    advertised = None
                if advertised is not None and advertised > max_bytes:
                    raise _video_too_large_error(max_bytes)
            written = 0
            with part.open("wb") as output:
                for chunk in response.iter_bytes():
                    written += len(chunk)
                    if written > max_bytes:
                        raise _video_too_large_error(max_bytes)
                    output.write(chunk)
        part.replace(destination)
    except ReadImageError:
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise ReadImageError(tr(f"下载视频失败：{exc}", f"Video download failed: {exc}")) from exc


def _is_video_media_error(exc: Exception) -> bool:
    if isinstance(exc, VisionMediaError):
        return True
    return is_media_error(
        getattr(exc, "status_code", None),
        getattr(exc, "error_code", None),
        getattr(exc, "detail", None) or str(exc),
    )


def _is_file_id_rejection(exc: Exception) -> bool:
    detail = getattr(exc, "detail", None) or ""
    error_code = getattr(exc, "error_code", None) or ""
    normalized = normalize_error_code(error_code)
    if any(
        marker in normalized
        for marker in (
            "fileid",
            "invalidfile",
            "missingfile",
            "filenotfound",
            "unsupportedfile",
        )
    ):
        return True
    text = f"{error_code} {detail}".lower()
    if "file_id" not in text and "file id" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "invalid",
            "missing",
            "not found",
            "not support",
            "unsupported",
            "不支持",
            "不存在",
            "无效",
        )
    )


def video_max_bytes() -> int:
    max_mb = max(1, env_int("READ_VIDEO_MAX_MB", DEFAULT_VIDEO_MAX_MB))
    return max_mb * 1024 * 1024


def _convert_video_to_mp4(input_path: Path, tmp_dir: Path) -> Path:
    output = tmp_dir / "converted.mp4"
    try:
        _transcode_video(input_path, output, crf=28)
    except ReadImageError as exc:
        raise ReadImageError(
            tr(
                "不支持此视频格式。请先用格式转换工具把视频转成 MP4 后再试。",
                "Unsupported video format. Convert the video to MP4 first.",
            )
        ) from exc
    return output


def _compress_video_to_limit(
    input_path: Path,
    tmp_dir: Path,
    *,
    max_bytes: int | None = None,
) -> Path:
    if max_bytes is None:
        max_bytes = video_max_bytes()
    output_720 = tmp_dir / "compressed-720.mp4"
    if output_720.resolve() == input_path.resolve():
        output_720 = tmp_dir / "base64-720.mp4"
    _transcode_video(
        input_path,
        output_720,
        scale=VIDEO_SCALE_720,
        crf=VIDEO_CRF_720,
    )
    if output_720.stat().st_size <= max_bytes:
        return output_720

    output_480 = tmp_dir / "compressed-480.mp4"
    if output_480.resolve() == input_path.resolve():
        output_480 = tmp_dir / "base64-480.mp4"
    _transcode_video(
        input_path,
        output_480,
        scale=VIDEO_SCALE_480,
        crf=VIDEO_CRF_480,
    )
    if output_480.stat().st_size <= max_bytes:
        return output_480

    raise _video_too_large_error(max_bytes)


def _ensure_video_within_base64_cap(path: Path, tmp_dir: Path) -> Path:
    max_bytes = video_base64_max_bytes()
    if path.stat().st_size > max_bytes:
        return _compress_video_to_limit(path, tmp_dir, max_bytes=max_bytes)
    return path


def _analyze_video_base64(
    path: Path,
    task: str,
    mode: str | None,
    tmp_dir: Path,
) -> str:
    path = _ensure_video_within_base64_cap(path, tmp_dir)
    data_url = _file_data_url(path)
    try:
        return _active_provider().call_video(data_url, task, mode)
    except ReadImageError as exc:
        if not _is_video_media_error(exc):
            raise
        converted = _convert_video_to_mp4(path, tmp_dir)
        converted = _ensure_video_within_base64_cap(converted, tmp_dir)
        converted_url = _file_data_url(converted)
        try:
            return _active_provider().call_video(converted_url, task, mode)
        except ReadImageError as exc2:
            if _is_video_media_error(exc2):
                raise ReadImageError(
                    tr(
                        "不支持此视频格式。请先用格式转换工具把视频转成 MP4 后再试。",
                        "Unsupported video format. Convert the video to MP4 first.",
                    )
                ) from exc2
            raise


def _delete_uploaded_video_file(file_id: str) -> bool:
    try:
        return _active_provider().delete_video_file(file_id)
    except Exception:
        return False


def _with_cleanup_warning(result: str) -> str:
    warning = tr(
        "（注意：视频临时文件清理失败）",
        "(note: video temporary file cleanup failed)",
    )
    return f"{result}\n{warning}"


def _analyze_local_video_files(
    path: Path,
    task: str,
    mode: str | None,
    tmp_dir: Path,
    depth: int = 0,
) -> str:
    try:
        file_id = _active_provider().upload_video_file(path)
    except ReadImageError:
        return _analyze_video_base64(path, task, mode, tmp_dir)

    cleaned = False
    try:
        try:
            result = _active_provider().call_video(
                "",
                task,
                mode,
                file_id=file_id,
            )
            cleanup_ok = _delete_uploaded_video_file(file_id)
            cleaned = True
            return result if cleanup_ok else _with_cleanup_warning(result)
        except ReadImageError as exc:
            if _is_file_id_rejection(exc):
                cleanup_ok = _delete_uploaded_video_file(file_id)
                cleaned = True
                result = _analyze_video_base64(path, task, mode, tmp_dir)
                return result if cleanup_ok else _with_cleanup_warning(result)
            if _is_video_media_error(exc):
                if depth >= MAX_VIDEO_CONVERSION_DEPTH:
                    raise ReadImageError(
                        tr(
                            "视频多次转换后仍无法识别。请先手动转成标准 MP4/H.264 后再试。",
                            "Video still cannot be recognized after repeated conversion. "
                            "Convert it to standard MP4/H.264 first.",
                        )
                    ) from exc
                converted = _convert_video_to_mp4(path, tmp_dir)
                if converted.stat().st_size > video_max_bytes():
                    converted = _compress_video_to_limit(converted, tmp_dir)
                return _analyze_local_video_files(
                    converted,
                    task,
                    mode,
                    tmp_dir,
                    depth + 1,
                )
            raise
    finally:
        if not cleaned:
            _delete_uploaded_video_file(file_id)


def _analyze_local_video(path: Path, task: str, mode: str | None, tmp_dir: Path) -> str:
    max_bytes = video_max_bytes()
    if path.stat().st_size > max_bytes:
        path = _compress_video_to_limit(path, tmp_dir)
    return _analyze_local_video_files(path, task, mode, tmp_dir)


def _analyze_remote_video(url: str, task: str, mode: str | None, tmp_dir: Path) -> str:
    validate_remote_url(url)
    max_bytes = video_max_bytes()
    remote_size = _remote_size(url)
    if remote_size is not None and remote_size > max_bytes:
        downloaded = tmp_dir / "downloaded-video"
        _download_video_url(url, downloaded)
        return _analyze_local_video(downloaded, task, mode, tmp_dir)

    try:
        return _active_provider().call_video(url, task, mode)
    except ReadImageError as exc:
        if not _is_video_media_error(exc):
            raise
        downloaded = tmp_dir / "downloaded-video"
        _download_video_url(url, downloaded)
        return _analyze_local_video(downloaded, task, mode, tmp_dir)


def analyze_video(video: str, task: str, mode: str | None) -> str:
    raw = video.strip()
    if not raw:
        raise ReadImageError(tr("video 参数为空。", "video argument is empty."))

    with tempfile.TemporaryDirectory(prefix="read-image-video-") as tmp:
        tmp_dir = Path(tmp)
        if raw.lower().startswith(("http://", "https://")):
            return _analyze_remote_video(raw, task, mode, tmp_dir)

        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.is_file():
            raise ReadImageError(tr(f"找不到视频文件：{path}", f"Video file not found: {path}"))
        return _analyze_local_video(path, task, mode, tmp_dir)
