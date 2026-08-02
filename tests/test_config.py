from __future__ import annotations

import pytest

from read_image.config import (
    DEFAULT_VIDEO_BASE64_MAX_MB,
    DEFAULT_VIDEO_DOWNLOAD_MAX_MB,
    DEFAULT_VIDEO_FILES_API_TIMEOUT_SEC,
    video_base64_max_bytes,
    video_download_max_bytes,
    video_files_api_timeout_sec,
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
