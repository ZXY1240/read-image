from __future__ import annotations

import os

DEFAULT_TASK = "详细描述图片内容"
DEFAULT_BATCH_TASK = "提取每张图片中的可见内容，并按图返回"
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seed-2-1-turbo-260628"
DEFAULT_MAX_DIMENSION = 2048
DEFAULT_IMAGE_FORMAT = "auto"
DEFAULT_JPEG_QUALITY = 90
DEFAULT_MODE = "standard"
DEFAULT_BATCH_WORKERS = 4
MAX_BATCH_WORKERS = 8
MAX_RATE_LIMIT_RETRIES = 4
MAX_TIMEOUT_RETRIES = 1
DEFAULT_VIDEO_TASK = "详细描述视频内容，按时间顺序说明关键画面、动作和字幕"
DEFAULT_VIDEO_MAX_MB = 50
DEFAULT_VIDEO_BASE64_MAX_MB = 45
DEFAULT_VIDEO_DOWNLOAD_MAX_MB = 512
DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC = 180
DEFAULT_VIDEO_TIMEOUT_SEC = 300
DEFAULT_CACHE_MAX_ENTRIES = 256

# Private personal plugin keeps this fallback. The public release strips it.
HARDCODED_API_KEY = ""


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def api_key() -> str:
    for name in (
        "READ_IMAGE_API_KEY",
        "ARK_API_KEY",
        "DOUBAO_API_KEY",
        "VISION_API_KEY",
    ):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    if HARDCODED_API_KEY:
        return HARDCODED_API_KEY
    from read_image.errors import tr

    raise RuntimeError(
        tr(
            "未找到豆包 API Key。请设置 ARK_API_KEY 或 READ_IMAGE_API_KEY 环境变量，"
            "并在 Codex 中重新加载 read-image 插件。",
            "Doubao API key not found. Set ARK_API_KEY or READ_IMAGE_API_KEY "
            "and reload read-image.",
        )
    )


def base_url() -> str:
    value = os.environ.get("READ_IMAGE_BASE_URL", "").strip().rstrip("/")
    return value or DEFAULT_BASE_URL


def model_name() -> str:
    return os.environ.get("READ_IMAGE_MODEL", "").strip() or DEFAULT_MODEL


def image_format() -> str:
    value = os.environ.get("READ_IMAGE_FORMAT", "").strip().lower()
    if value in {"jpeg", "jpg"}:
        return "jpeg"
    return DEFAULT_IMAGE_FORMAT


def cache_max_entries() -> int:
    return max(0, env_int("READ_IMAGE_CACHE_MAX_ENTRIES", DEFAULT_CACHE_MAX_ENTRIES))


def video_base64_max_bytes() -> int:
    max_mb = max(1, env_int("READ_VIDEO_BASE64_MAX_MB", DEFAULT_VIDEO_BASE64_MAX_MB))
    return max_mb * 1024 * 1024


def video_download_max_bytes() -> int:
    max_mb = max(
        1,
        env_int(
            "READ_VIDEO_DOWNLOAD_MAX_MB",
            DEFAULT_VIDEO_DOWNLOAD_MAX_MB,
        ),
    )
    return max_mb * 1024 * 1024


def video_files_api_timeout_sec() -> int:
    return max(
        1,
        env_int(
            "READ_VIDEO_FILES_API_TIMEOUT_SEC",
            DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC,
        ),
    )
