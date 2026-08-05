from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnimodal.errors import ReadImageError
from omnimodal.profiles import (
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
    tmp_path: Path,
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
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text(json.dumps(overrides), encoding="utf-8")
    monkeypatch.setattr("omnimodal.profiles.profile_override_path", lambda: profile_path)
    quick = profile_for_mode("quick")
    assert quick.thinking_enabled is True
    assert quick.max_tokens == 99
    assert quick.timeout_sec == 12
    assert quick.system_prompt == "custom image prompt"
    assert video_prompt_for_mode("quick") == "custom video prompt"


def test_profiles_json_prompt_applies_to_video_without_video_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overrides = {"standard": {"prompt": "shared prompt"}}
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text(json.dumps(overrides), encoding="utf-8")
    monkeypatch.setattr("omnimodal.profiles.profile_override_path", lambda: profile_path)
    assert profile_for_mode("standard").system_prompt == "shared prompt"
    assert video_prompt_for_mode("standard") == "shared prompt"


def test_profiles_json_invalid_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr("omnimodal.profiles.profile_override_path", lambda: profile_path)
    with pytest.raises(ReadImageError):
        profile_for_mode("standard")
