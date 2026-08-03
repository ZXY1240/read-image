from __future__ import annotations

from pathlib import Path

import pytest

from read_image.config import (
    DEFAULT_VIDEO_BASE64_MAX_MB,
    DEFAULT_VIDEO_DOWNLOAD_MAX_MB,
    DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC,
    DEFAULT_VIDEO_WORKERS,
    api_key,
    cache_use_task,
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
    monkeypatch.delenv("READ_VIDEO_BASE64_MAX_MB", raising=False)
    monkeypatch.delenv("READ_VIDEO_DOWNLOAD_MAX_MB", raising=False)
    monkeypatch.delenv("READ_VIDEO_FILES_API_TIMEOUT_SEC", raising=False)
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
    monkeypatch.setenv("READ_VIDEO_BASE64_MAX_MB", "2")
    monkeypatch.setenv("READ_VIDEO_DOWNLOAD_MAX_MB", "3")
    monkeypatch.setenv("READ_VIDEO_FILES_API_TIMEOUT_SEC", "90")
    assert video_base64_max_bytes() == 2 * 1024 * 1024
    assert video_download_max_bytes() == 3 * 1024 * 1024
    assert video_files_api_timeout_sec() == 90
    monkeypatch.setenv("READ_IMAGE_VIDEO_WORKERS", "3")
    assert video_worker_count() == 3


def test_api_key_requires_env_or_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_ENV_FILE", str(tmp_path / "missing.env"))
    for name in (
        "READ_IMAGE_API_KEY",
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
    env_file.write_text("READ_IMAGE_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.setenv("READ_IMAGE_ENV_FILE", str(env_file))
    for name in (
        "READ_IMAGE_API_KEY",
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
    env_file.write_text("READ_IMAGE_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.setenv("READ_IMAGE_ENV_FILE", str(env_file))
    monkeypatch.setenv("READ_IMAGE_API_KEY", "system-key")
    assert api_key() == "system-key"


def test_video_keep_audio_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("READ_VIDEO_KEEP_AUDIO", raising=False)
    assert video_keep_audio() is False
    monkeypatch.setenv("READ_VIDEO_KEEP_AUDIO", "1")
    assert video_keep_audio() is True


def test_provider_defaults_to_doubao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("READ_IMAGE_PROVIDER", raising=False)
    monkeypatch.delenv("READ_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("READ_IMAGE_MODEL", raising=False)
    assert provider_name() == "doubao"


def test_provider_auto_uses_openai_compatible_when_base_and_model_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("READ_IMAGE_PROVIDER", raising=False)
    monkeypatch.setenv("READ_IMAGE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("READ_IMAGE_MODEL", "glm-5v-turbo")
    assert provider_name() == "openai_compatible"


def test_provider_explicit_env_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_PROVIDER", "openai_compatible")
    monkeypatch.setenv("READ_IMAGE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("READ_IMAGE_MODEL", "qwen3-vl-plus")
    assert provider_name() == "openai_compatible"
    monkeypatch.setenv("READ_IMAGE_PROVIDER", "doubao")
    assert provider_name() == "doubao"


def test_video_worker_count_prefers_new_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_VIDEO_WORKERS", "5")
    monkeypatch.setenv("READ_IMAGE_VIDEO_WORKERS", "2")
    assert video_worker_count() == 5
    monkeypatch.delenv("READ_VIDEO_WORKERS")
    assert video_worker_count() == 2


def test_cache_use_task_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("READ_IMAGE_CACHE_USE_TASK", raising=False)
    assert cache_use_task() is False
    monkeypatch.setenv("READ_IMAGE_CACHE_USE_TASK", "1")
    assert cache_use_task() is True


def test_openai_thinking_param_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("READ_IMAGE_OPENAI_THINKING_PARAM", raising=False)
    assert openai_thinking_param() == "auto"
    monkeypatch.setenv("READ_IMAGE_OPENAI_THINKING_PARAM", "enable_thinking")
    assert openai_thinking_param() == "enable_thinking"
