from __future__ import annotations

import json

import pytest

from read_image.errors import ReadImageError
from read_image.profiles import (
    normalize_mode,
    profile_for_mode,
    video_prompt_for_mode,
    video_timeout_for_mode,
)


def test_normalize_default_mode() -> None:
    assert normalize_mode(None) == "standard"
    assert normalize_mode("") == "standard"


def test_normalize_aliases() -> None:
    assert normalize_mode("快速识别") == "quick"
    assert normalize_mode("balanced") == "balanced_analysis"
    assert normalize_mode("deep") == "deep_analysis"


def test_invalid_mode_raises() -> None:
    with pytest.raises(ReadImageError):
        normalize_mode("not-a-mode")


def test_profile_parameters() -> None:
    quick = profile_for_mode("quick")
    deep = profile_for_mode("deep_analysis")
    assert quick.thinking_enabled is False
    assert quick.max_tokens == 512
    assert deep.thinking_enabled is True
    assert deep.max_tokens is None


def test_video_profiles_use_longer_timeouts() -> None:
    assert video_timeout_for_mode("quick") == 90
    assert video_timeout_for_mode("deep_analysis") == 600
    assert "视频" in video_prompt_for_mode("standard")


def test_profiles_json_overrides_profile_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overrides = {
        "quick": {
            "thinking": True,
            "max_tokens": 99,
            "timeout_sec": 12,
            "prompt": "custom image prompt",
            "video_prompt": "custom video prompt",
        }
    }
    monkeypatch.setenv("READ_IMAGE_PROFILES_JSON", json.dumps(overrides))
    quick = profile_for_mode("quick")
    assert quick.thinking_enabled is True
    assert quick.max_tokens == 99
    assert quick.timeout_sec == 12
    assert quick.system_prompt == "custom image prompt"
    assert video_prompt_for_mode("quick") == "custom video prompt"


def test_profiles_json_prompt_applies_to_video_without_video_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overrides = {"standard": {"prompt": "shared prompt"}}
    monkeypatch.setenv("READ_IMAGE_PROFILES_JSON", json.dumps(overrides))
    assert profile_for_mode("standard").system_prompt == "shared prompt"
    assert video_prompt_for_mode("standard") == "shared prompt"


def test_profiles_json_invalid_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_PROFILES_JSON", "{not-json")
    with pytest.raises(ReadImageError):
        profile_for_mode("standard")
