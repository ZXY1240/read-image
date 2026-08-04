from __future__ import annotations

from typing import Any

from omnimodal.config import ocr_model_name, openai_thinking_param
from omnimodal.profiles import profile_for_mode, video_prompt_for_mode
from omnimodal.providers.base import VisionProvider


class OpenAICompatibleProvider(VisionProvider):
    """Generic OpenAI-compatible vision provider for GLM, Qwen, and similar APIs."""

    provider_name = "openai_compatible"
    supports_video_files = False

    def _thinking_fields(self, enabled: bool, model: str | None = None) -> dict[str, Any]:
        style = openai_thinking_param()
        target = model or self.model
        if style == "auto":
            style = "enable_thinking" if target.lower().startswith("qwen") else "thinking"
        if style == "enable_thinking":
            return {"enable_thinking": enabled}
        if style == "thinking":
            return {"thinking": {"type": "enabled" if enabled else "disabled"}}
        return {}

    def build_payload(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        if file_id is not None:
            raise NotImplementedError("OpenAI-compatible providers do not use Files API.")
        profile = profile_for_mode(mode)
        if kind == "video":
            system_prompt = video_prompt_for_mode(profile.key)
        else:
            system_prompt = profile.system_prompt
        # mode=ocr 时用 OCR 专用模型（qwen-vl-ocr 0.3/0.5 元/M，比通用视觉便宜）；
        # 视频不走 OCR 模型。
        model = ocr_model_name() if (profile.key == "ocr" and kind != "video") else self.model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": task},
                        self._content_item(kind, content_url),
                    ],
                },
            ],
        }
        payload.update(self._thinking_fields(profile.thinking_enabled, model))
        if profile.max_tokens is not None:
            payload["max_tokens"] = profile.max_tokens
        return payload
