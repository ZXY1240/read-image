from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omnimodal import api
from omnimodal.config import DEFAULT_MODEL
from omnimodal.errors import (
    ReadImageError,
    VisionTimeoutError,
)
from omnimodal.providers import base as provider_base
from omnimodal.providers.openai_compatible import OpenAICompatibleProvider

pytestmark = pytest.mark.usefixtures("fake_api_key")


def _client_with_handler(
    handler: Any,
    provider: OpenAICompatibleProvider | None = None,
) -> tuple[api.VisionClient, httpx.Client]:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return api.VisionClient(provider=provider, client=client), client


def test_build_image_payload() -> None:
    client = api.VisionClient()
    payload = client.build_payload("image", "data:image/jpeg;base64,AAAA", "task", "standard")
    assert payload["model"] == DEFAULT_MODEL
    assert payload["enable_thinking"] is False
    assert payload["max_tokens"] == 2048
    content = payload["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "task"}
    assert content[1]["type"] == "image_url"


def test_build_video_payload() -> None:
    client = api.VisionClient()
    payload = client.build_payload(
        "video", "https://example.invalid/v.mp4", "task", "deep_analysis"
    )
    assert payload["enable_thinking"] is True
    assert "max_tokens" not in payload
    content = payload["messages"][1]["content"]
    assert content[1]["type"] == "video_url"


def test_build_audio_payload() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.7-flash",
    )
    payload = provider.build_payload(
        "audio",
        "https://example.invalid/a.mp3",
        "task",
        "quick",
    )
    content = payload["messages"][1]["content"]
    assert content[1]["type"] == "input_audio"
    assert content[1]["input_audio"] == {
        "data": "https://example.invalid/a.mp3",
        "format": "mp3",
    }


def test_call_image_posts_to_chat_completions() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    client, _ = _client_with_handler(handler)
    assert client.call_image(b"bytes", "task", "quick") == "ok"
    body = json.loads(captured["request"].content)
    assert body["model"] == DEFAULT_MODEL
    assert body["messages"][1]["content"][1]["type"] == "image_url"


def test_call_audio_posts_to_chat_completions() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    client, _ = _client_with_handler(handler)
    assert client.call_audio("https://example.invalid/a.mp3", "task", "quick") == "ok"
    body = json.loads(captured["request"].content)
    assert body["messages"][1]["content"][1]["type"] == "input_audio"


def test_429_is_retried_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                json={"error": {"message": "slow"}},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    monkeypatch.setattr(provider_base.time, "sleep", lambda _: None)
    client, _ = _client_with_handler(handler)
    assert client.call_image(b"bytes", "task", "quick") == "ok"
    assert calls == 2


def test_timeout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionTimeoutError):
        client.call_image(b"bytes", "task", "quick")


def test_error_detail_is_stored_and_redacted() -> None:
    secret = "sk-test-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": f"bad {secret}"}},
        )

    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.7-flash",
    )
    client, _ = _client_with_handler(handler, provider=provider)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_image(b"bytes", "task", "quick")
    assert secret not in str(exc_info.value)


def test_openai_provider_does_not_support_video_files() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1",
        "qwen3.7-flash",
    )
    with pytest.raises(ReadImageError):
        provider.upload_video_file("missing.mp4")
