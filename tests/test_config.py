from __future__ import annotations

from pathlib import Path

import pytest

from omnimodal.config import (
    DEFAULT_VIDEO_BASE64_MAX_MB,
    DEFAULT_VIDEO_DOWNLOAD_MAX_MB,
    DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC,
    DEFAULT_VIDEO_WORKERS,
    api_key,
    cache_ttl_sec,
    cache_use_task,
    drag_dirs,
    drag_patterns,
    drag_window_minutes,
    extreme_aspect_ratio_limit,
    openai_thinking_param,
    provider_name,
    video_base64_max_bytes,
    video_download_max_bytes,
    video_files_api_timeout_sec,
    video_keep_audio,
    video_worker_count,
)


def test_video_files_api_config_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNIMODAL_VIDEO_BASE64_MAX_MB", raising=False)
    monkeypatch.delenv("OMNIMODAL_VIDEO_DOWNLOAD_MAX_MB", raising=False)
    monkeypatch.delenv("OMNIMODAL_VIDEO_FILES_API_TIMEOUT_SEC", raising=False)
    assert DEFAULT_VIDEO_BASE64_MAX_MB == 45
    assert DEFAULT_VIDEO_DOWNLOAD_MAX_MB == 512
    assert DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC == 180
    assert DEFAULT_VIDEO_WORKERS == 2
    assert video_base64_max_bytes() == 45 * 1024 * 1024
    assert video_download_max_bytes() == 512 * 1024 * 1024
    assert video_files_api_timeout_sec() == 180
    assert video_worker_count() == 2


def test_video_files_api_config_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_VIDEO_BASE64_MAX_MB", "2")
    monkeypatch.setenv("OMNIMODAL_VIDEO_DOWNLOAD_MAX_MB", "3")
    monkeypatch.setenv("OMNIMODAL_VIDEO_FILES_API_TIMEOUT_SEC", "90")
    assert video_base64_max_bytes() == 2 * 1024 * 1024
    assert video_download_max_bytes() == 3 * 1024 * 1024
    assert video_files_api_timeout_sec() == 90
    monkeypatch.setenv("OMNIMODAL_VIDEO_WORKERS", "3")
    assert video_worker_count() == 3


def test_api_key_requires_env_or_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_ENV_FILE", str(tmp_path / "missing.env"))
    for name in (
        "OMNIMODAL_API_KEY",
        "ARK_API_KEY",
        "DOUBAO_API_KEY",
        "VISION_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError):
        api_key()


def test_api_key_loads_from_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OMNIMODAL_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.setenv("OMNIMODAL_ENV_FILE", str(env_file))
    for name in (
        "OMNIMODAL_API_KEY",
        "ARK_API_KEY",
        "DOUBAO_API_KEY",
        "VISION_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    assert api_key() == "dotenv-key"


def test_system_env_wins_over_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OMNIMODAL_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.setenv("OMNIMODAL_ENV_FILE", str(env_file))
    monkeypatch.setenv("OMNIMODAL_API_KEY", "system-key")
    assert api_key() == "system-key"


def test_video_keep_audio_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIMODAL_VIDEO_KEEP_AUDIO", raising=False)
    assert video_keep_audio() is False
    monkeypatch.setenv("OMNIMODAL_VIDEO_KEEP_AUDIO", "1")
    assert video_keep_audio() is True


def test_provider_defaults_to_openai_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNIMODAL_PROVIDER", raising=False)
    monkeypatch.delenv("OMNIMODAL_BASE_URL", raising=False)
    monkeypatch.delenv("OMNIMODAL_IMAGE_MODEL", raising=False)
    assert provider_name() == "qwen"


def test_provider_auto_uses_openai_compatible_when_base_and_model_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OMNIMODAL_IMAGE_MODEL", "qwen3.7-plus")
    assert provider_name() == "qwen"


def test_provider_explicit_env_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OMNIMODAL_IMAGE_MODEL", "qwen3-vl-plus")
    assert provider_name() == "qwen"


def test_video_worker_count_prefers_new_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_VIDEO_WORKERS", "5")
    assert video_worker_count() == 5
    monkeypatch.delenv("OMNIMODAL_VIDEO_WORKERS")
    assert video_worker_count() == 2


def test_video_worker_count_invalid_first_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 第一个变量非法时继续尝试第二个，而不是直接返回默认值
    monkeypatch.setenv("OMNIMODAL_VIDEO_WORKERS", "abc")
    assert video_worker_count() == 2
    monkeypatch.delenv("OMNIMODAL_VIDEO_WORKERS")
    assert video_worker_count() == 2


def test_cache_use_task_defaults_to_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNIMODAL_CACHE_USE_TASK", raising=False)
    assert cache_use_task() is True
    monkeypatch.setenv("OMNIMODAL_CACHE_USE_TASK", "1")
    assert cache_use_task() is True
    monkeypatch.setenv("OMNIMODAL_CACHE_USE_TASK", "0")
    assert cache_use_task() is False


def test_cache_ttl_sec_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNIMODAL_CACHE_TTL_SEC", raising=False)
    assert cache_ttl_sec() == 300
    monkeypatch.setenv("OMNIMODAL_CACHE_TTL_SEC", "60")
    assert cache_ttl_sec() == 60


def test_drag_config_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNIMODAL_DRAG_WINDOW_MIN", raising=False)
    monkeypatch.delenv("OMNIMODAL_DRAG_PATTERNS", raising=False)
    monkeypatch.delenv("OMNIMODAL_DRAG_DIRS", raising=False)
    assert drag_window_minutes() == 30
    assert "*.tmp" in drag_patterns()
    assert drag_dirs()

    monkeypatch.setenv("OMNIMODAL_DRAG_WINDOW_MIN", "10")
    monkeypatch.setenv("OMNIMODAL_DRAG_PATTERNS", "claude-*,*.tmp")
    monkeypatch.setenv("OMNIMODAL_DRAG_DIRS", "C:\\custom-drag")
    assert drag_window_minutes() == 10
    assert drag_patterns() == ["claude-*", "*.tmp"]
    assert drag_dirs()[-1] == "C:\\custom-drag"


def test_openai_thinking_param_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNIMODAL_OPENAI_THINKING_PARAM", raising=False)
    assert openai_thinking_param() == "auto"
    monkeypatch.setenv("OMNIMODAL_OPENAI_THINKING_PARAM", "enable_thinking")
    assert openai_thinking_param() == "enable_thinking"


def test_extreme_aspect_ratio_limit_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNIMODAL_EXTREME_ASPECT_RATIO_LIMIT", raising=False)
    assert extreme_aspect_ratio_limit() == 8
    monkeypatch.setenv("OMNIMODAL_EXTREME_ASPECT_RATIO_LIMIT", "12")
    assert extreme_aspect_ratio_limit() == 12
    monkeypatch.setenv("OMNIMODAL_EXTREME_ASPECT_RATIO_LIMIT", "0")
    assert extreme_aspect_ratio_limit() == float("inf")
