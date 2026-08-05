from __future__ import annotations

from typing import Any

from omnimodal.config import (
    audio_model,
    image_model,
    ocr_model_name,
    openai_thinking_param,
    video_model,
)
from omnimodal.profiles import module_prompt_for_mode, profile_for_mode
from omnimodal.providers.base import VisionProvider


class OpenAICompatibleProvider(VisionProvider):
    """Qwen/DashScope OpenAI-compatible provider for image, video, and audio."""

    provider_name = "qwen"
    supports_video_files = False

    def __init__(
        self,
        base_url: str,
        model: str,
        client=None,
        *,
        provider_name: str | None = None,
    ):
        super().__init__(base_url, model, client=client)
        if provider_name:
            self.provider_name = provider_name

    def _is_omni_model(self) -> bool:
        model = self.model.lower()
        return any(
            marker in model
            for marker in (
                "qwen3.5-omni",
                "qwen3-omni",
                "qwen-omni",
            )
        )

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
        system_prompt = module_prompt_for_mode("video" if kind == "video" else "image", mode)
        if profile.key == "ocr" and kind != "video":
            model = ocr_model_name()
        elif kind == "audio":
            model = self.model if self._is_omni_model() else audio_model()
        elif kind == "video":
            model = self.model if self._is_omni_model() else video_model()
        else:
            model = image_model()
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

    def call_audio(
        self,
        audio_url: str,
        task: str,
        mode: str | None,
        timeout_sec: int | None = None,
    ) -> str:
        if self._is_omni_model():
            from omnimodal.audio_processing import analyze_audio

            tier = "pro" if "plus" in self.model.lower() else "standard"
            return analyze_audio(
                audio_url,
                task=task,
                mode=mode,
                tier=tier,
                model=self.model,
            )
        return super().call_audio(
            audio_url,
            task,
            mode,
            timeout_sec=timeout_sec,
        )


class ZaiProvider(OpenAICompatibleProvider):
    """Z.AI / GLM OpenAI-compatible provider for image, video, and audio."""

    provider_name = "zai"
    supports_video_files = False

    def __init__(
        self,
        base_url: str,
        model: str,
        client=None,
    ):
        super().__init__(base_url, model, client=client, provider_name="zai")

    def _is_omni_model(self) -> bool:
        model = self.model.lower()
        return any(marker in model for marker in ("glm", "zai"))

    def call_audio(
        self,
        audio_url: str,
        task: str,
        mode: str | None,
        timeout_sec: int | None = None,
    ) -> str:
        # GLM 的音频理解也走 Chat input_audio，不需要 DashScope 流式实现。
        return super(OpenAICompatibleProvider, self).call_audio(
            audio_url,
            task,
            mode,
            timeout_sec=timeout_sec,
        )
