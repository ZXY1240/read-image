from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from read_image.config import DEFAULT_MODE
from read_image.errors import ReadImageError, tr


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    thinking_enabled: bool
    max_tokens: int | None
    timeout_sec: int
    system_prompt: str
    video_prompt: str


VIDEO_PROMPTS: dict[str, str] = {
    "quick": "你是视频识别工具。只输出与任务直接相关的关键内容，简短回答，不要展开。",
    "standard": (
        "你是视频识别工具。按时间顺序描述视频中与任务相关的内容，"
        "保留关键画面、动作、字幕和场景变化。"
    ),
    "full": (
        "你是视频识别工具。完整、无遗漏地描述视频内容，包括画面、动作、字幕、场景和所有可见细节。"
    ),
    "quick_analysis": "你是视频分析助手。快速给出结论、依据和注意点。",
    "balanced_analysis": "你是视频分析助手。结构化输出结论、依据、例外和风险。",
    "deep_analysis": "你是视频分析助手。深度分析上下文、证据、推理、风险和结论。",
}

VIDEO_TIMEOUTS: dict[str, int] = {
    "quick": 90,
    "standard": 180,
    "full": 360,
    "quick_analysis": 180,
    "balanced_analysis": 360,
    "deep_analysis": 600,
}


PROFILES: dict[str, Profile] = {
    "quick": Profile(
        key="quick",
        label="快速识别",
        thinking_enabled=False,
        max_tokens=512,
        timeout_sec=30,
        system_prompt=(
            "你是视觉识别工具。只提取与任务直接相关的内容，简短回答，"
            "不要解释、不要展开、不要输出多余说明。"
        ),
        video_prompt=VIDEO_PROMPTS["quick"],
    ),
    "standard": Profile(
        key="standard",
        label="标准提取",
        thinking_enabled=False,
        max_tokens=2048,
        timeout_sec=60,
        system_prompt=(
            "你是视觉提取工具。完整提取图片中与任务相关的内容，保留原文、"
            "表格和必要结构，不要跳过关键细节。"
        ),
        video_prompt=VIDEO_PROMPTS["standard"],
    ),
    "full": Profile(
        key="full",
        label="完整提取",
        thinking_enabled=False,
        max_tokens=None,
        timeout_sec=180,
        system_prompt=(
            "你是视觉提取工具。完整、无遗漏地提取图片内容，尽量保留 Markdown "
            "结构、表格、代码块和所有可见细节。"
        ),
        video_prompt=VIDEO_PROMPTS["full"],
    ),
    "quick_analysis": Profile(
        key="quick_analysis",
        label="快速分析",
        thinking_enabled=True,
        max_tokens=512,
        timeout_sec=90,
        system_prompt=(
            "你是视觉分析助手。结合图片快速给出结论，只输出关键判断、依据和需要注意的点。"
        ),
        video_prompt=VIDEO_PROMPTS["quick_analysis"],
    ),
    "balanced_analysis": Profile(
        key="balanced_analysis",
        label="平衡分析",
        thinking_enabled=True,
        max_tokens=2048,
        timeout_sec=180,
        system_prompt=("你是视觉分析助手。结合图片给出结构化分析，包含结论、依据、例外和风险。"),
        video_prompt=VIDEO_PROMPTS["balanced_analysis"],
    ),
    "deep_analysis": Profile(
        key="deep_analysis",
        label="深度分析",
        thinking_enabled=True,
        max_tokens=None,
        timeout_sec=300,
        system_prompt=(
            "你是视觉分析助手。结合图片进行深入分析，覆盖上下文、证据、推理过程、风险、局限和结论。"
        ),
        video_prompt=VIDEO_PROMPTS["deep_analysis"],
    ),
}

MODE_ALIASES: dict[str, str] = {
    "quick": "quick",
    "fast": "quick",
    "fast_ocr": "quick",
    "快速识别": "quick",
    "standard": "standard",
    "标准提取": "standard",
    "标准": "standard",
    "full": "full",
    "完整提取": "full",
    "完整": "full",
    "quick_analysis": "quick_analysis",
    "fast_analysis": "quick_analysis",
    "快速分析": "quick_analysis",
    "balanced_analysis": "balanced_analysis",
    "balanced": "balanced_analysis",
    "平衡分析": "balanced_analysis",
    "deep_analysis": "deep_analysis",
    "deep": "deep_analysis",
    "深度分析": "deep_analysis",
}


def normalize_mode(mode: str | None) -> str:
    if mode is None:
        return DEFAULT_MODE
    raw = str(mode).strip()
    if not raw:
        return DEFAULT_MODE
    key = raw.lower().replace("-", "_").replace(" ", "_")
    if key in MODE_ALIASES:
        return MODE_ALIASES[key]
    if raw in MODE_ALIASES:
        return MODE_ALIASES[raw]
    choices = "、".join(PROFILES)
    raise ReadImageError(
        tr(
            f"未知档位：{raw}。可选：{choices}",
            f"Unknown mode: {raw}. Choices: {', '.join(PROFILES)}",
        )
    )


def _profile_overrides() -> dict[str, dict[str, Any]]:
    raw = os.environ.get("READ_IMAGE_PROFILES_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReadImageError(
            tr(
                "READ_IMAGE_PROFILES_JSON 不是合法 JSON。",
                "READ_IMAGE_PROFILES_JSON is not valid JSON.",
            )
        ) from exc
    if not isinstance(parsed, dict):
        raise ReadImageError(
            tr(
                "READ_IMAGE_PROFILES_JSON 必须是对象。",
                "READ_IMAGE_PROFILES_JSON must be an object.",
            )
        )
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in parsed.items():
        if not isinstance(value, dict):
            raise ReadImageError(
                tr(
                    f"档位覆盖配置必须是对象：{key}",
                    f"Profile override must be an object: {key}",
                )
            )
        normalized[normalize_mode(str(key))] = value
    return normalized


def _merge_profile(profile: Profile, overrides: dict[str, Any]) -> Profile:
    thinking = bool(overrides.get("thinking", profile.thinking_enabled))
    max_tokens = overrides.get("max_tokens", profile.max_tokens)
    if max_tokens is not None:
        max_tokens = int(max_tokens)
    timeout_sec = int(overrides.get("timeout_sec", profile.timeout_sec))
    prompt = str(overrides.get("prompt", profile.system_prompt))
    if "video_prompt" in overrides:
        video_prompt = str(overrides["video_prompt"])
    elif "prompt" in overrides:
        video_prompt = prompt
    else:
        video_prompt = profile.video_prompt
    return Profile(
        key=profile.key,
        label=profile.label,
        thinking_enabled=thinking,
        max_tokens=max_tokens,
        timeout_sec=timeout_sec,
        system_prompt=prompt,
        video_prompt=video_prompt,
    )


def profile_for_mode(mode: str | None) -> Profile:
    key = normalize_mode(mode)
    overrides = _profile_overrides().get(key, {})
    return _merge_profile(PROFILES[key], overrides)


def video_prompt_for_mode(mode: str | None) -> str:
    return profile_for_mode(mode).video_prompt


def video_timeout_for_mode(mode: str | None) -> int:
    return VIDEO_TIMEOUTS[normalize_mode(mode)]
