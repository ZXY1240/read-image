from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omnimodal.errors import ReadImageError
from omnimodal.providers.factory import create_provider
from omnimodal.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ZaiProvider,
)

pytestmark = pytest.mark.usefixtures("fake_api_key")


def test_create_provider_defaults_to_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIMODAL_BASE_URL", raising=False)
    monkeypatch.delenv("OMNIMODAL_IMAGE_MODEL", raising=False)
    provider = create_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_name == "qwen"
    assert provider.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert provider.model == "qwen3.7-flash"


def test_create_provider_respects_omnimodal_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIMODAL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OMNIMODAL_IMAGE_MODEL", "qwen3.7-plus")
    provider = create_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://example.invalid/v1"
    assert provider.model == "qwen3.7-plus"


def test_openai_qwen_payload_uses_enable_thinking() -> None:
    provider = OpenAICompatibleProvider(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-flash",
    )
    payload = provider.build_payload(
        "image",
        "data:image/jpeg;base64,AAAA",
        "task",
        "quick",
    )
    assert payload["enable_thinking"] is False


def test_openai_video_payload_uses_video_url() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.7-flash",
    )
    payload = provider.build_payload(
        "video",
        "https://example.invalid/video.mp4",
        "task",
        "quick",
    )
    content = payload["messages"][1]["content"]
    assert content[1]["type"] == "video_url"
    assert content[1]["video_url"] == {"url": "https://example.invalid/video.mp4"}


def test_openai_audio_payload_uses_input_audio() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.7-flash",
    )
    payload = provider.build_payload(
        "audio",
        "https://example.invalid/audio.mp3",
        "task",
        "quick",
    )
    content = payload["messages"][1]["content"]
    assert content[1]["type"] == "input_audio"
    assert content[1]["input_audio"] == {
        "data": "https://example.invalid/audio.mp3",
        "format": "mp3",
    }
    assert payload["model"] == "qwen3.5-omni-flash"


def test_openai_omni_audio_payload_keeps_provider_model_and_base64_format() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.5-omni-plus",
    )
    payload = provider.build_payload(
        "audio",
        "data:audio/wav;base64,AAAA",
        "task",
        "quick",
    )
    content = payload["messages"][1]["content"]
    assert payload["model"] == "qwen3.5-omni-plus"
    assert content[1]["type"] == "input_audio"
    assert content[1]["input_audio"] == {
        "data": "data:audio/wav;base64,AAAA",
        "format": "wav",
    }


def test_openai_compatible_provider_does_not_support_files_api() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.7-flash",
    )
    assert provider.supports_video_files is False
    with pytest.raises(ReadImageError):
        provider.upload_video_file("not-a-path")


def test_openai_compatible_call_posts_to_chat_completions() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.7-flash",
        client=client,
    )
    assert provider.call_image(b"bytes", "task", "quick") == "ok"
    request = captured["request"]
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["authorization"].startswith("Bearer ")
    body = json.loads(request.content)
    assert body["model"] == "qwen3.7-flash"
    assert body["messages"][1]["content"][1]["type"] == "image_url"


def test_openai_compatible_oss_resource_header_only_for_oss_urls() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.7-flash",
        client=client,
    )

    provider.call_video("oss://omnimodal/test.mp4", "task", "quick")
    provider.call_video("https://example.invalid/video.mp4", "task", "quick")

    assert calls[0].headers["x-dashscope-ossresourceresolve"] == "enable"
    assert "x-dashscope-ossresourceresolve" not in calls[1].headers


def test_create_provider_zai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    monkeypatch.setenv("OMNIMODAL_BASE_URL", "https://api.z.ai/api/paas/v4")
    monkeypatch.setenv("OMNIMODAL_IMAGE_MODEL", "glm-5v-turbo")
    provider = create_provider()
    assert isinstance(provider, ZaiProvider)
    assert provider.provider_name == "zai"
    assert provider.base_url == "https://api.z.ai/api/paas/v4"
    assert provider.model == "glm-5v-turbo"


def test_create_provider_openai_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OMNIMODAL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OMNIMODAL_IMAGE_MODEL", "gpt-4o-mini")
    provider = create_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_name == "openai_compatible"
    assert provider.model == "gpt-4o-mini"


def test_zai_payload_uses_glm_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    monkeypatch.setenv("OMNIMODAL_IMAGE_MODEL", "glm-5v-turbo")
    provider = ZaiProvider("https://api.z.ai/api/paas/v4", "glm-5v-turbo")
    image_payload = provider.build_payload(
        "image",
        "data:image/jpeg;base64,AAAA",
        "task",
        "quick",
    )
    assert image_payload["model"] == "glm-5v-turbo"
    audio_payload = provider.build_payload(
        "audio",
        "https://example.invalid/a.mp3",
        "task",
        "quick",
    )
    assert audio_payload["model"] == "glm-5v-turbo"
    assert audio_payload["messages"][1]["content"][1]["type"] == "input_audio"


def test_zai_audio_call_uses_chat_not_dashscope_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ZaiProvider(
        "https://api.z.ai/api/paas/v4",
        "glm-5v-turbo",
        client=client,
    )
    assert provider.call_audio("https://example.invalid/a.mp3", "task", "quick") == "ok"
    request = captured["request"]
    assert request.url.path.endswith("/chat/completions")
    body = json.loads(request.content)
    assert body["messages"][1]["content"][1]["type"] == "input_audio"


def test_openai_compatible_audio_payload_uses_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OMNIMODAL_AUDIO_MODEL_STANDARD", "gpt-4o-audio-preview")
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "gpt-4o-mini",
        provider_name="openai_compatible",
    )
    payload = provider.build_payload(
        "audio",
        "https://example.invalid/a.mp3",
        "task",
        "quick",
    )
    assert payload["model"] == "gpt-4o-audio-preview"
