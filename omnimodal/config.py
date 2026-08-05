from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from omnimodal.errors import ReadImageError, tr

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
LOCAL_CONFIG_PATH = CONFIG_DIR / "local.json"
DEFAULT_OUTPUT_DIR = Path.home() / ".omnimodal" / "outputs"
DEFAULT_PROBE_DIR = Path.home() / ".omnimodal" / "probes"

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_IMAGE_MODEL = "qwen3.7-flash"
DEFAULT_ZAI_IMAGE_MODEL = "glm-5v-turbo"
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-4o-mini"
DEFAULT_MODEL = DEFAULT_IMAGE_MODEL
DEFAULT_VIDEO_MODEL = "qwen3.7-flash"
DEFAULT_ZAI_VIDEO_MODEL = "glm-5v-turbo"
DEFAULT_OPENAI_VIDEO_MODEL = "gpt-4o-mini"
DEFAULT_AUDIO_MODEL = "qwen3.5-omni-flash"
DEFAULT_ZAI_AUDIO_MODEL = "glm-5v-turbo"
DEFAULT_OPENAI_AUDIO_MODEL = "gpt-4o-audio-preview"
DEFAULT_OCR_MODEL = "qwen3.5-ocr"
DEFAULT_ZAI_OCR_MODEL = "glm-ocr"
DEFAULT_OPENAI_OCR_MODEL = "gpt-4o-mini"
DEFAULT_ASR_MODEL = "fun-asr"
DEFAULT_ZAI_ASR_MODEL = "glm-asr-2512"
DEFAULT_OPENAI_ASR_MODEL = "whisper-1"
DEFAULT_AUDIO_UNDERSTANDING_MAX_SEC = 300

PROVIDER_CHOICES = ("dashscope", "zai", "openai_compatible")

DEFAULT_TASK = "详细描述图片内容"
DEFAULT_BATCH_TASK = "提取每个媒体中的可见内容，并按输入顺序返回"
DEFAULT_VIDEO_TASK = "详细描述视频内容，按时间顺序说明关键画面、动作和字幕"
DEFAULT_AUDIO_TASK = "详细描述这段音频的内容"
DEFAULT_MODE = "standard"
DEFAULT_BATCH_WORKERS = 4
MAX_BATCH_WORKERS = 8
MAX_RATE_LIMIT_RETRIES = 4
MAX_TIMEOUT_RETRIES = 1

DEFAULT_MAX_DIMENSION = 2048
DEFAULT_IMAGE_FORMAT = "auto"
DEFAULT_JPEG_QUALITY = 90
DEFAULT_EXTREME_ASPECT_RATIO_LIMIT = 8
DEFAULT_CACHE_MAX_ENTRIES = 256
DEFAULT_CACHE_USE_TASK = True
DEFAULT_CACHE_TTL_SEC = 300
DEFAULT_OPENAI_THINKING_PARAM = "auto"

DEFAULT_VIDEO_MAX_MB = 50
DEFAULT_VIDEO_BASE64_MAX_MB = 45
DEFAULT_VIDEO_DOWNLOAD_MAX_MB = 512
DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC = 180
DEFAULT_VIDEO_KEEP_AUDIO = False
DEFAULT_VIDEO_WORKERS = 2
DEFAULT_VIDEO_TIMEOUT_SEC = 300

DEFAULT_DRAG_WINDOW_MIN = 30
DEFAULT_DRAG_PATTERNS = (
    "codex-clipboard-*",
    "pasted_image*",
    "current_paste*",
    "pasted_*",
    "claude*",
    "*.tmp",
)

DEFAULT_IMAGE_GEN_TIMEOUT_SEC = 300
DEFAULT_VIDEO_GEN_TIMEOUT_SEC = 1800
DEFAULT_AUDIO_GEN_TIMEOUT_SEC = 600
DEFAULT_ASR_TIMEOUT_SEC = 1800
DEFAULT_MAX_VIDEO_DURATION = 15
DEFAULT_GENERATION_OUTPUT_DIR = str(DEFAULT_OUTPUT_DIR)

_LOCAL_CONFIG: dict[str, Any] = {}


def _load_local_config() -> None:
    global _LOCAL_CONFIG
    if not LOCAL_CONFIG_PATH.is_file():
        return
    try:
        parsed = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(parsed, dict):
        _LOCAL_CONFIG = parsed


_load_local_config()


def _load_local_env_file() -> None:
    configured = os.environ.get("OMNIMODAL_ENV_FILE", "").strip()
    env_file = configured or str(ROOT / ".env")
    load_dotenv(env_file, override=False)


_load_local_env_file()


def env(name: str, default: str = "") -> str:
    """Read a string setting from the environment or config/local.json."""
    raw = os.environ.get(name)
    if raw is not None and raw.strip():
        return raw.strip()
    value = _LOCAL_CONFIG.get(name)
    if value is not None and str(value).strip():
        return str(value).strip()
    return default


def env_int(name: str, default: int) -> int:
    raw = env(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name, "").lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def api_key() -> str:
    _load_local_env_file()
    value = env("OMNIMODAL_API_KEY")
    if not value:
        from omnimodal.errors import tr

        raise RuntimeError(
            tr(
                "未找到 Omnimodal API Key。请在插件根目录 .env 中设置 OMNIMODAL_API_KEY。",
                "Omnimodal API key not found. Set OMNIMODAL_API_KEY in the plugin .env file.",
            )
        )
    return value


def provider() -> str:
    """Return the active provider: dashscope, zai, or openai_compatible."""
    value = env("OMNIMODAL_PROVIDER", "dashscope").lower().strip()
    return value if value in PROVIDER_CHOICES else "dashscope"


def base_url() -> str:
    defaults = {
        "zai": DEFAULT_ZAI_BASE_URL,
        "openai_compatible": DEFAULT_OPENAI_BASE_URL,
        "dashscope": DEFAULT_BASE_URL,
    }
    return env("OMNIMODAL_BASE_URL", defaults[provider()]).rstrip("/")


def image_model() -> str:
    defaults = {
        "zai": DEFAULT_ZAI_IMAGE_MODEL,
        "openai_compatible": DEFAULT_OPENAI_IMAGE_MODEL,
        "dashscope": DEFAULT_IMAGE_MODEL,
    }
    return env("OMNIMODAL_IMAGE_MODEL", defaults[provider()])


def model_name() -> str:
    return image_model()


def provider_name() -> str:
    return {
        "dashscope": "qwen",
        "zai": "zai",
        "openai_compatible": "openai_compatible",
    }[provider()]


def video_model() -> str:
    defaults = {
        "zai": DEFAULT_ZAI_VIDEO_MODEL,
        "openai_compatible": DEFAULT_OPENAI_VIDEO_MODEL,
        "dashscope": DEFAULT_VIDEO_MODEL,
    }
    return env("OMNIMODAL_VIDEO_MODEL", defaults[provider()])


def audio_model(tier: str | None = None) -> str:
    name = tier if tier in {"pro", "max"} else "standard"
    defaults = {
        "zai": DEFAULT_ZAI_AUDIO_MODEL,
        "openai_compatible": DEFAULT_OPENAI_AUDIO_MODEL,
        "dashscope": (DEFAULT_AUDIO_MODEL if name == "standard" else "qwen3.5-omni-plus"),
    }
    return env(f"OMNIMODAL_AUDIO_MODEL_{name.upper()}", defaults[provider()])


def ocr_model_name() -> str:
    defaults = {
        "zai": DEFAULT_ZAI_OCR_MODEL,
        "openai_compatible": DEFAULT_OPENAI_OCR_MODEL,
        "dashscope": DEFAULT_OCR_MODEL,
    }
    return env("OMNIMODAL_OCR_MODEL", defaults[provider()])


def asr_model_name() -> str:
    defaults = {
        "zai": DEFAULT_ZAI_ASR_MODEL,
        "openai_compatible": DEFAULT_OPENAI_ASR_MODEL,
        "dashscope": DEFAULT_ASR_MODEL,
    }
    return env("OMNIMODAL_ASR_MODEL", defaults[provider()])


def openai_thinking_param() -> str:
    value = env("OMNIMODAL_OPENAI_THINKING_PARAM", DEFAULT_OPENAI_THINKING_PARAM).lower()
    if value in {"auto", "thinking", "enable_thinking", "none"}:
        return value
    return DEFAULT_OPENAI_THINKING_PARAM


def image_format() -> str:
    value = env("OMNIMODAL_IMAGE_FORMAT", DEFAULT_IMAGE_FORMAT).lower()
    if value in {"jpeg", "jpg"}:
        return "jpeg"
    return DEFAULT_IMAGE_FORMAT


def extreme_aspect_ratio_limit() -> float:
    raw = env("OMNIMODAL_EXTREME_ASPECT_RATIO_LIMIT", "").lower()
    if not raw:
        return float(DEFAULT_EXTREME_ASPECT_RATIO_LIMIT)
    if raw in {"0", "off", "false", "no", "none"}:
        return float("inf")
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_EXTREME_ASPECT_RATIO_LIMIT)
    return value if value > 0 else float("inf")


def max_dimension() -> int:
    return max(1, env_int("OMNIMODAL_MAX_DIMENSION", DEFAULT_MAX_DIMENSION))


def jpeg_quality() -> int:
    return min(100, max(1, env_int("OMNIMODAL_JPEG_QUALITY", DEFAULT_JPEG_QUALITY)))


def cache_max_entries() -> int:
    return max(0, env_int("OMNIMODAL_CACHE_MAX_ENTRIES", DEFAULT_CACHE_MAX_ENTRIES))


def cache_use_task() -> bool:
    return env_bool("OMNIMODAL_CACHE_USE_TASK", DEFAULT_CACHE_USE_TASK)


def cache_ttl_sec() -> int:
    return max(0, env_int("OMNIMODAL_CACHE_TTL_SEC", DEFAULT_CACHE_TTL_SEC))


def drag_window_minutes() -> int:
    return max(1, env_int("OMNIMODAL_DRAG_WINDOW_MIN", DEFAULT_DRAG_WINDOW_MIN))


def drag_patterns() -> list[str]:
    raw = env("OMNIMODAL_DRAG_PATTERNS", "")
    if not raw:
        return list(DEFAULT_DRAG_PATTERNS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def drag_dirs() -> list[str]:
    dirs = [tempfile.gettempdir()]
    configured = env("OMNIMODAL_DRAG_DIRS", "")
    for raw in configured.split(";"):
        raw = raw.strip()
        if raw:
            dirs.append(raw)
    return dirs


def video_base64_max_bytes() -> int:
    max_mb = max(1, env_int("OMNIMODAL_VIDEO_BASE64_MAX_MB", DEFAULT_VIDEO_BASE64_MAX_MB))
    return max_mb * 1024 * 1024


def video_download_max_bytes() -> int:
    max_mb = max(
        1,
        env_int("OMNIMODAL_VIDEO_DOWNLOAD_MAX_MB", DEFAULT_VIDEO_DOWNLOAD_MAX_MB),
    )
    return max_mb * 1024 * 1024


def video_files_api_timeout_sec() -> int:
    return max(
        1,
        env_int(
            "OMNIMODAL_VIDEO_FILES_API_TIMEOUT_SEC",
            DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC,
        ),
    )


def video_keep_audio() -> bool:
    return env_bool("OMNIMODAL_VIDEO_KEEP_AUDIO", DEFAULT_VIDEO_KEEP_AUDIO)


def allow_private_urls() -> bool:
    return env_bool("OMNIMODAL_ALLOW_PRIVATE_URLS", False)


def video_worker_count() -> int:
    return max(1, env_int("OMNIMODAL_VIDEO_WORKERS", DEFAULT_VIDEO_WORKERS))


def audio_understanding_max_sec() -> int:
    return max(
        30,
        env_int(
            "OMNIMODAL_AUDIO_UNDERSTANDING_MAX_SEC",
            DEFAULT_AUDIO_UNDERSTANDING_MAX_SEC,
        ),
    )


def default_tier() -> str:
    value = env("OMNIMODAL_DEFAULT_TIER", "standard").lower()
    return value if value in {"standard", "pro", "max"} else "standard"


def generation_output_dir() -> Path:
    raw = env("OMNIMODAL_GENERATION_OUTPUT_DIR", DEFAULT_GENERATION_OUTPUT_DIR)
    return Path(raw).expanduser().resolve()


def probe_dir() -> Path:
    return Path(env("OMNIMODAL_PROBE_DIR", str(DEFAULT_PROBE_DIR))).expanduser().resolve()


def generation_timeout_sec(kind: str) -> int:
    if kind == "video":
        return max(60, env_int("OMNIMODAL_VIDEO_GEN_TIMEOUT_SEC", DEFAULT_VIDEO_GEN_TIMEOUT_SEC))
    if kind == "audio":
        return max(60, env_int("OMNIMODAL_AUDIO_GEN_TIMEOUT_SEC", DEFAULT_AUDIO_GEN_TIMEOUT_SEC))
    return max(60, env_int("OMNIMODAL_IMAGE_GEN_TIMEOUT_SEC", DEFAULT_IMAGE_GEN_TIMEOUT_SEC))


def asr_timeout_sec() -> int:
    return max(60, env_int("OMNIMODAL_ASR_TIMEOUT_SEC", DEFAULT_ASR_TIMEOUT_SEC))


def max_video_duration() -> int:
    return max(1, env_int("OMNIMODAL_MAX_VIDEO_DURATION", DEFAULT_MAX_VIDEO_DURATION))


def generation_model() -> str:
    """Optional generic generation model override for compatible providers."""
    return env("OMNIMODAL_GENERATION_MODEL", "")


def video_generation_base_url() -> str:
    return env("OMNIMODAL_VIDEO_GEN_BASE_URL", base_url()).rstrip("/")


def audio_generation_base_url() -> str:
    return env("OMNIMODAL_AUDIO_GEN_BASE_URL", base_url()).rstrip("/")


def image_generation_model(tier: str) -> str:
    active = provider()
    if active != "dashscope":
        env_name = f"OMNIMODAL_IMAGE_GEN_MODEL_{tier.upper()}"
        default = (
            generation_model()
            or {
                "zai": "glm-image",
                "openai_compatible": "gpt-image-1",
            }[active]
        )
        return env(env_name, default)
    if tier == "pro":
        return env("OMNIMODAL_IMAGE_GEN_MODEL_PRO", "wan2.7-image-pro")
    if tier == "max":
        return env("OMNIMODAL_IMAGE_GEN_MODEL_MAX", "qwen-image-3.0-pro")
    return env("OMNIMODAL_IMAGE_GEN_MODEL_STANDARD", "qwen-image-3.0")


def video_generation_model(tier: str, kind: str = "t2v") -> str:
    active = provider()
    if active == "zai":
        env_name = f"OMNIMODAL_VIDEO_GEN_MODEL_{tier.upper()}"
        if kind == "i2v":
            env_name += "_I2V"
        elif kind == "edit":
            env_name = "OMNIMODAL_VIDEO_GEN_MODEL_EDIT"
        return env(env_name, generation_model() or "cogvideox-3")
    if active == "openai_compatible":
        raise ReadImageError(
            tr(
                "OpenAI 兼容 Provider 不支持视频生成。",
                "OpenAI-compatible provider does not support video generation.",
            )
        )
    if kind == "i2v":
        if tier == "max":
            return env("OMNIMODAL_VIDEO_GEN_MODEL_MAX_I2V", "happyhorse-1.1-i2v")
        return env("OMNIMODAL_VIDEO_GEN_MODEL_STANDARD_I2V", "wan2.7-i2v")
    if kind == "edit":
        return env("OMNIMODAL_VIDEO_GEN_MODEL_EDIT", "wan2.7-videoedit")
    if tier == "max":
        return env("OMNIMODAL_VIDEO_GEN_MODEL_MAX", "happyhorse-1.1-t2v")
    return env("OMNIMODAL_VIDEO_GEN_MODEL_STANDARD", "wan2.7-t2v")


def audio_generation_model(tier: str) -> str:
    if tier == "pro":
        return env("OMNIMODAL_AUDIO_GEN_MODEL_PRO", "cosyvoice-v3.5-plus")
    if tier == "max":
        return env("OMNIMODAL_AUDIO_GEN_MODEL_MAX", "qwen-audio-3.0-tts-flash")
    return env("OMNIMODAL_AUDIO_GEN_MODEL_STANDARD", "qwen3-tts-instruct-flash")


def model_catalog_path() -> Path:
    return Path(env("OMNIMODAL_MODEL_CATALOG_JSON", str(CONFIG_DIR / "model_catalog.json")))


def profile_override_path() -> Path:
    return Path(env("OMNIMODAL_PROFILES_JSON", str(CONFIG_DIR / "profiles.json")))


def language() -> str:
    return env("OMNIMODAL_LANGUAGE", "zh").lower() or "zh"


def log_level() -> str:
    return env("OMNIMODAL_LOG_LEVEL", "INFO")


def allowed_output_dirs() -> list[str]:
    raw = env("OMNIMODAL_ALLOWED_OUTPUT_DIRS", "")
    return [item.strip() for item in raw.split(";") if item.strip()]


def windows_capture_dir() -> str:
    return env("OMNIMODAL_WINDOWS_CAPTURE_DIR", "")


__all__ = [
    "DEFAULT_BATCH_TASK",
    "DEFAULT_BATCH_WORKERS",
    "DEFAULT_MODE",
    "DEFAULT_TASK",
    "DEFAULT_VIDEO_TASK",
    "DEFAULT_AUDIO_TASK",
    "MAX_BATCH_WORKERS",
    "MAX_RATE_LIMIT_RETRIES",
    "MAX_TIMEOUT_RETRIES",
    "allow_private_urls",
    "allowed_output_dirs",
    "api_key",
    "asr_model_name",
    "asr_timeout_sec",
    "audio_generation_model",
    "audio_model",
    "audio_understanding_max_sec",
    "base_url",
    "cache_max_entries",
    "cache_ttl_sec",
    "cache_use_task",
    "default_tier",
    "drag_dirs",
    "drag_patterns",
    "drag_window_minutes",
    "env",
    "env_bool",
    "env_int",
    "extreme_aspect_ratio_limit",
    "generation_output_dir",
    "generation_timeout_sec",
    "generation_model",
    "image_format",
    "image_generation_model",
    "image_model",
    "jpeg_quality",
    "language",
    "log_level",
    "max_dimension",
    "max_video_duration",
    "model_catalog_path",
    "ocr_model_name",
    "openai_thinking_param",
    "probe_dir",
    "provider",
    "profile_override_path",
    "audio_generation_base_url",
    "video_base64_max_bytes",
    "video_download_max_bytes",
    "video_files_api_timeout_sec",
    "video_generation_model",
    "video_generation_base_url",
    "video_keep_audio",
    "video_model",
    "video_worker_count",
    "windows_capture_dir",
]
