from __future__ import annotations

from pathlib import Path

import pytest

from read_image.config import (
    DEFAULT_VIDEO_BASE64_MAX_MB,
    DEFAULT_VIDEO_DOWNLOAD_MAX_MB,
    DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC,
    api_key,
    video_base64_max_bytes,
    video_download_max_bytes,
    video_files_api_timeout_sec,
    video_keep_audio,
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
    assert video_base64_max_bytes() == 45 * 1024 * 1024
    assert video_download_max_bytes() == 512 * 1024 * 1024
    assert video_files_api_timeout_sec() == 180


def test_video_files_api_config_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_VIDEO_BASE64_MAX_MB", "2")
    monkeypatch.setenv("READ_VIDEO_DOWNLOAD_MAX_MB", "3")
    monkeypatch.setenv("READ_VIDEO_FILES_API_TIMEOUT_SEC", "90")
    assert video_base64_max_bytes() == 2 * 1024 * 1024
    assert video_download_max_bytes() == 3 * 1024 * 1024
    assert video_files_api_timeout_sec() == 90


def test_api_key_requires_env_or_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
