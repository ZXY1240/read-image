from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from read_image.errors import ReadImageError
from read_image.providers.doubao import DoubaoProvider
from read_image.providers.factory import create_provider
from read_image.providers.openai_compatible import OpenAICompatibleProvider


def test_create_provider_defaults_to_doubao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("READ_IMAGE_PROVIDER", raising=False)
    monkeypatch.delenv("READ_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("READ_IMAGE_MODEL", raising=False)
    assert isinstance(create_provider(), DoubaoProvider)


def test_create_provider_selects_openai_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_PROVIDER", "openai_compatible")
    monkeypatch.setenv("READ_IMAGE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("READ_IMAGE_MODEL", "glm-5v-turbo")
    provider = create_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://example.invalid/v1"
    assert provider.model == "glm-5v-turbo"


def test_create_provider_openai_compatible_requires_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_PROVIDER", "openai_compatible")
    monkeypatch.delenv("READ_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("READ_IMAGE_MODEL", raising=False)
    with pytest.raises(ReadImageError):
        create_provider()


def test_doubao_payload_keeps_thinking_object() -> None:
    provider = DoubaoProvider(
        "https://ark.cn-beijing.volces.com/api/v3",
        "doubao-seed-2-1-turbo-260628",
    )
    payload = provider.build_payload(
        "image",
        "data:image/jpeg;base64,AAAA",
        "task",
        "quick",
    )
    assert payload["thinking"]["type"] == "disabled"
    assert payload["max_tokens"] == 512


def test_openai_qwen_payload_uses_enable_thinking() -> None:
    provider = OpenAICompatibleProvider(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3-vl-plus",
    )
    payload = provider.build_payload(
        "image",
        "data:image/jpeg;base64,AAAA",
        "task",
        "quick",
    )
    assert payload["enable_thinking"] is False


def test_openai_glm_payload_uses_thinking_object() -> None:
    provider = OpenAICompatibleProvider(
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-5v-turbo",
    )
    payload = provider.build_payload(
        "image",
        "data:image/jpeg;base64,AAAA",
        "task",
        "quick",
    )
    assert payload["thinking"]["type"] == "disabled"


def test_openai_video_payload_uses_video_url() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3-omni-flash",
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


def test_openai_compatible_provider_does_not_support_files_api() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3-vl-plus",
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
        "glm-5v-turbo",
        client=client,
    )
    assert provider.call_image(b"bytes", "task", "quick") == "ok"
    request = captured["request"]
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["authorization"].startswith("Bearer ")
    body = json.loads(request.content)
    assert body["model"] == "glm-5v-turbo"
    assert body["messages"][1]["content"][1]["type"] == "image_url"
