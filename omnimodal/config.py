from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

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
DEFAULT_VIDEO_KEEP_AUDIO = False
DEFAULT_VIDEO_WORKERS = 2
DEFAULT_VIDEO_TIMEOUT_SEC = 300
DEFAULT_CACHE_MAX_ENTRIES = 256
DEFAULT_CACHE_USE_TASK = True
DEFAULT_CACHE_TTL_SEC = 300
DEFAULT_PROVIDER = "doubao"
DEFAULT_OPENAI_THINKING_PARAM = "auto"
DEFAULT_EXTREME_ASPECT_RATIO_LIMIT = 8
DEFAULT_DRAG_WINDOW_MIN = 30
DEFAULT_DRAG_PATTERNS = (
    "codex-clipboard-*",
    "pasted_image*",
    "current_paste*",
    "pasted_*",
    "claude*",
    "*.tmp",
)


def _load_local_env_file() -> None:
    configured = os.environ.get("READ_IMAGE_ENV_FILE", "").strip()
    env_file = configured or str(Path(__file__).resolve().parents[1] / ".env")
    load_dotenv(env_file, override=False)


_load_local_env_file()


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def api_key() -> str:
    _load_local_env_file()
    for name in (
        "READ_IMAGE_API_KEY",
        "ARK_API_KEY",
        "DOUBAO_API_KEY",
        "VISION_API_KEY",
    ):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    from omnimodal.errors import tr

    raise RuntimeError(
        tr(
            "未找到豆包 API Key。请在插件根目录 .env 文件中设置 "
            "READ_IMAGE_API_KEY，或设置 ARK_API_KEY 环境变量，"
            "并在 Codex 中重新加载 omnimodal 插件。",
            "Doubao API key not found. Set READ_IMAGE_API_KEY in the plugin "
            ".env file or set ARK_API_KEY, then reload omnimodal.",
        )
    )


def base_url() -> str:
    value = os.environ.get("READ_IMAGE_BASE_URL", "").strip().rstrip("/")
    return value or DEFAULT_BASE_URL


def model_name() -> str:
    return os.environ.get("READ_IMAGE_MODEL", "").strip() or DEFAULT_MODEL


def ocr_model_name() -> str:
    """OCR 专用模型（mode=ocr 时使用，比通用视觉更便宜：qwen-vl-ocr 0.3/0.5 元/M）。"""
    return os.environ.get("READ_IMAGE_OCR_MODEL", "").strip() or "qwen-vl-ocr"


def provider_name() -> str:
    value = os.environ.get("READ_IMAGE_PROVIDER", "").strip().lower()
    if value in {"doubao", "openai_compatible"}:
        return value
    if (
        os.environ.get("READ_IMAGE_BASE_URL", "").strip()
        and os.environ.get("READ_IMAGE_MODEL", "").strip()
    ):
        return "openai_compatible"
    return DEFAULT_PROVIDER


def openai_thinking_param() -> str:
    value = os.environ.get("READ_IMAGE_OPENAI_THINKING_PARAM", "").strip().lower()
    if value in {"auto", "thinking", "enable_thinking", "none"}:
        return value
    return DEFAULT_OPENAI_THINKING_PARAM


def extreme_aspect_ratio_limit() -> float:
    raw = os.environ.get("READ_IMAGE_EXTREME_ASPECT_RATIO_LIMIT", "").strip()
    if not raw:
        return float(DEFAULT_EXTREME_ASPECT_RATIO_LIMIT)
    lowered = raw.lower()
    if lowered in {"0", "off", "false", "no", "none"}:
        return float("inf")
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_EXTREME_ASPECT_RATIO_LIMIT)
    return value if value > 0 else float("inf")


def image_format() -> str:
    value = os.environ.get("READ_IMAGE_FORMAT", "").strip().lower()
    if value in {"jpeg", "jpg"}:
        return "jpeg"
    return DEFAULT_IMAGE_FORMAT


def cache_max_entries() -> int:
    return max(0, env_int("READ_IMAGE_CACHE_MAX_ENTRIES", DEFAULT_CACHE_MAX_ENTRIES))


def cache_use_task() -> bool:
    raw = os.environ.get("READ_IMAGE_CACHE_USE_TASK", "").strip().lower()
    if not raw:
        return DEFAULT_CACHE_USE_TASK
    return raw not in {
        "0",
        "false",
        "no",
        "off",
    }


def cache_ttl_sec() -> int:
    return max(0, env_int("READ_IMAGE_CACHE_TTL_SEC", DEFAULT_CACHE_TTL_SEC))


def drag_window_minutes() -> int:
    return max(1, env_int("READ_DRAG_WINDOW_MIN", DEFAULT_DRAG_WINDOW_MIN))


def drag_patterns() -> list[str]:
    raw = os.environ.get("READ_DRAG_PATTERNS", "").strip()
    if not raw:
        return list(DEFAULT_DRAG_PATTERNS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def drag_dirs() -> list[str]:
    dirs = [tempfile.gettempdir()]
    configured = os.environ.get("READ_DRAG_DIRS", "").strip()
    for raw in configured.split(";"):
        raw = raw.strip()
        if raw:
            dirs.append(raw)
    return dirs


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


def video_keep_audio() -> bool:
    return os.environ.get("READ_VIDEO_KEEP_AUDIO", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def allow_private_urls() -> bool:
    return os.environ.get("READ_IMAGE_ALLOW_PRIVATE_URLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def video_worker_count() -> int:
    for name in ("READ_VIDEO_WORKERS", "READ_IMAGE_VIDEO_WORKERS"):
        raw = os.environ.get(name, "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                return DEFAULT_VIDEO_WORKERS
    return DEFAULT_VIDEO_WORKERS
