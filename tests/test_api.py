from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnimodal import api, http
from omnimodal.config import DEFAULT_MODEL
from omnimodal.errors import (
    ReadImageError,
    VisionMediaError,
    VisionRateLimitError,
    VisionTimeoutError,
)
from omnimodal.providers import base as provider_base
from omnimodal.providers import doubao as provider_doubao

pytestmark = pytest.mark.usefixtures("fake_api_key")


def _json_response(
    status_code: int,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(status_code, json=payload, headers=headers)


def _client_with_handler(
    handler: Any,
) -> tuple[api.VisionClient, httpx.Client]:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return api.VisionClient(client=http_client), http_client


def test_build_image_payload() -> None:
    client = api.VisionClient()
    payload = client.build_payload("image", "data:image/jpeg;base64,AAAA", "task", "standard")
    assert payload["model"] == DEFAULT_MODEL
    assert payload["thinking"]["type"] == "disabled"
    assert payload["max_tokens"] == 2048
    content = payload["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "task"}
    assert content[1]["type"] == "image_url"


def test_build_video_payload() -> None:
    client = api.VisionClient()
    payload = client.build_payload(
        "video", "https://example.invalid/v.mp4", "task", "deep_analysis"
    )
    assert payload["thinking"]["type"] == "enabled"
    assert "max_tokens" not in payload
    content = payload["messages"][1]["content"]
    assert content[1]["type"] == "video_url"


def test_call_video_file_id_builds_file_id_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _json_response(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
        )

    client, _ = _client_with_handler(handler)
    assert client.call_video_file_id("file-abc", "task", "quick") == "ok"
    body = json.loads(captured["request"].content)
    video_url = body["messages"][1]["content"][1]["video_url"]
    assert video_url == {"file_id": "file-abc"}


def test_module_call_video_file_id_uses_default_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _json_response(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "default_client", api.VisionClient(client=http_client))
    assert api.call_video_file_id("file-abc", "task", "quick") == "ok"
    body = json.loads(captured["request"].content)
    video_url = body["messages"][1]["content"][1]["video_url"]
    assert video_url == {"file_id": "file-abc"}


def test_upload_video_file_posts_multipart_and_returns_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _json_response(
            200,
            {
                "id": "file-abc",
                "object": "file",
                "bytes": len(b"fake-video"),
                "created_at": 123,
                "filename": video.name,
                "purpose": "user_data",
                "status": "processed",
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    assert api.upload_video_file(video) == "file-abc"
    request = captured["request"]
    assert request.method == "POST"
    assert request.url.path.endswith("/files")
    assert request.headers["authorization"].startswith("Bearer ")
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert b'name="purpose"' in body
    assert b"user_data" in body
    assert b"tiny.mp4" in body
    assert b"fake-video" in body
    assert b"content-type: video/mp4" in body.lower()


def test_delete_video_file_calls_delete_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    assert api.delete_video_file("file-abc") is True
    request = captured["request"]
    assert request.method == "DELETE"
    assert request.url.path.endswith("/files/file-abc")


def test_delete_video_file_swallows_cleanup_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(http.logger, "propagate", True)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            500,
            {"error": {"message": "cleanup failed"}},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    assert api.delete_video_file("file-abc") is False
    assert "cleanup failed" in caplog.text


def test_delete_video_file_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return _json_response(500, {"error": {"message": "retry"}})
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    assert api.delete_video_file("file-abc", retries=2) is True
    assert calls == 3


def test_delete_video_file_does_not_log_api_detail(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(http.logger, "propagate", True)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            500,
            {"error": {"message": "SECRET_DETAIL_XYZ"}},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    assert api.delete_video_file("file-abc") is False
    assert "SECRET_DETAIL_XYZ" not in caplog.text


def test_redact_sensitive_text_hides_query_params_and_data_urls() -> None:
    redacted = api._redact_sensitive_text(
        "https://example.invalid/?token=SECRET data:image/png;base64,AAAA"
    )
    assert "token=SECRET" not in redacted
    assert "AAAA" not in redacted
    assert "token=[REDACTED]" in redacted


def test_upload_video_file_raises_on_http_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _json_response(
            500,
            {"error": {"code": "InternalError", "message": "upload failed"}},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    with pytest.raises(ReadImageError):
        api.upload_video_file(video)
    assert calls == 1


def test_upload_video_file_raises_when_poll_status_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_LANGUAGE", "en")
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "uploaded",
                },
            )
        if request.method == "GET" and request.url.path.endswith("/files/file-abc"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "failed",
                },
            )
        if request.method == "DELETE" and request.url.path.endswith("/files/file-abc"):
            return httpx.Response(204)
        raise AssertionError(request.method, request.url.path)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    with pytest.raises(ReadImageError) as exc_info:
        api.upload_video_file(video)
    assert "processing failed" in str(exc_info.value)


def test_upload_video_file_deletes_uploaded_file_on_poll_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_LANGUAGE", "en")
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")
    deleted: list[str] = []
    monkeypatch.setattr(
        provider_doubao.DoubaoProvider,
        "delete_video_file",
        lambda self, file_id, timeout_sec=30, retries=2: deleted.append(file_id),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "uploaded",
                },
            )
        if request.method == "GET" and request.url.path.endswith("/files/file-abc"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "failed",
                },
            )
        raise AssertionError(request.method, request.url.path)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    with pytest.raises(ReadImageError):
        api.upload_video_file(video)
    assert deleted == ["file-abc"]


def test_upload_video_file_uses_one_budget_when_upload_response_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_LANGUAGE", "en")
    clock = [0.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    deleted: list[str] = []
    monkeypatch.setattr(
        provider_doubao.DoubaoProvider,
        "delete_video_file",
        lambda self, file_id, timeout_sec=30, retries=2: deleted.append(file_id),
    )
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")

    def handler(request: httpx.Request) -> httpx.Response:
        clock[0] = 100.0
        return _json_response(
            200,
            {
                "id": "file-abc",
                "status": "uploaded",
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    with pytest.raises(ReadImageError) as exc_info:
        api.upload_video_file(video, timeout_sec=1)
    assert "processing timed out" in str(exc_info.value)
    assert deleted == ["file-abc"]


def test_upload_video_file_uses_one_budget_during_polling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_LANGUAGE", "en")
    clock = [0.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "uploaded",
                },
            )
        if request.method == "GET" and request.url.path.endswith("/files/file-abc"):
            clock[0] = 100.0
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "processing",
                },
            )
        if request.method == "DELETE" and request.url.path.endswith("/files/file-abc"):
            return httpx.Response(204)
        raise AssertionError(request.method, request.url.path)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    with pytest.raises(ReadImageError) as exc_info:
        api.upload_video_file(video, timeout_sec=1)
    assert "processing timed out" in str(exc_info.value)


def test_upload_video_file_429_near_deadline_raises_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_LANGUAGE", "en")
    clock = [0.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    monkeypatch.setattr(api, "MAX_RATE_LIMIT_RETRIES", 4)
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        clock[0] = 100.0
        return _json_response(
            429,
            {"error": {"message": "slow"}},
            headers={"Retry-After": "0"},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    with pytest.raises(ReadImageError) as exc_info:
        api.upload_video_file(video, timeout_sec=1)
    assert "processing timed out" in str(exc_info.value)
    assert calls == 1


def test_api_error_detail_is_stored_and_not_rendered_in_str() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {
                "error": {
                    "code": "InvalidParameter",
                    "message": "file_id not supported",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_video_file_id("file-abc", "task", "quick")
    assert exc_info.value.detail == "file_id not supported"
    assert "file_id not supported" not in str(exc_info.value)


def test_upload_video_file_polls_until_processed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")
    calls: dict[str, int] = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "object": "file",
                    "bytes": len(b"fake-video"),
                    "created_at": 123,
                    "filename": video.name,
                    "purpose": "user_data",
                    "status": "uploaded",
                },
            )
        if request.method == "GET" and request.url.path.endswith("/files/file-abc"):
            calls["poll"] += 1
            status = "processing" if calls["poll"] == 1 else "processed"
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "object": "file",
                    "bytes": len(b"fake-video"),
                    "created_at": 123,
                    "filename": video.name,
                    "purpose": "user_data",
                    "status": status,
                },
            )
        raise AssertionError(request.method, request.url.path)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    assert api.upload_video_file(video) == "file-abc"
    assert calls["poll"] == 2


def test_upload_video_file_retries_rate_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_doubao, "MAX_RATE_LIMIT_RETRIES", 1)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"fake-video")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _json_response(
                429,
                {"error": {"message": "slow"}},
                headers={"Retry-After": "0"},
            )
        return _json_response(
            200,
            {
                "id": "file-abc",
                "object": "file",
                "bytes": len(b"fake-video"),
                "created_at": 123,
                "filename": video.name,
                "purpose": "user_data",
                "status": "processed",
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    monkeypatch.setattr(api, "http_client", http_client)
    assert api.upload_video_file(video) == "file-abc"
    assert calls == 2


def test_call_parses_success_response() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _json_response(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
        )

    client, _ = _client_with_handler(handler)
    assert client.call_image(b"bytes", "task", "quick", mime_type="image/jpeg") == "ok"
    body = json.loads(captured["request"].content)
    url = body["messages"][1]["content"][1]["image_url"]["url"]
    assert url == "data:image/jpeg;base64,Ynl0ZXM="
    assert captured["request"].headers["authorization"].startswith("Bearer ")


def test_call_image_sends_provided_mime_type() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _json_response(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
        )

    client, _ = _client_with_handler(handler)
    assert client.call_image(b"fake", "task", "quick", mime_type="image/png") == "ok"
    body = json.loads(captured["request"].content)
    url = body["messages"][1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_rate_limit_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_base, "MAX_RATE_LIMIT_RETRIES", 1)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _json_response(
                429,
                {"error": {"message": "slow"}},
                headers={"Retry-After": "0"},
            )
        return _json_response(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
        )

    client, _ = _client_with_handler(handler)
    assert client.call_video("data:video/mp4;base64,AAAA", "task", "quick") == "ok"
    assert calls == 2


def test_timeout_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_base, "MAX_TIMEOUT_RETRIES", 1)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timed out")
        return _json_response(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
        )

    client, _ = _client_with_handler(handler)
    assert client.call_image(b"bytes", "task", "quick") == "ok"
    assert calls == 2


def test_invalid_response_raises_read_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"choices": []})

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError):
        client.call_image(b"bytes", "task", "quick")


def test_http_error_exposes_status_and_json_error_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {
                "error": {
                    "code": "InvalidParameter",
                    "message": "invalid base64 video_url",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionMediaError) as exc_info:
        client.call_video("https://example.invalid/v.mp4", "task", "quick")
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "InvalidParameter"


def test_unsupported_media_status_raises_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            415,
            {
                "error": {
                    "code": "UnsupportedMediaType",
                    "message": "media type is not supported",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionMediaError) as exc_info:
        client.call_video("https://example.invalid/v.mp4", "task", "quick")
    assert exc_info.value.status_code == 415
    assert exc_info.value.error_code == "UnsupportedMediaType"


def test_unsupported_media_status_is_authoritative_without_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            415,
            {"error": {"message": "media type is not supported"}},
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionMediaError) as exc_info:
        client.call_video("https://example.invalid/v.mp4", "task", "quick")
    assert exc_info.value.status_code == 415


def test_explicit_media_code_is_authoritative_on_422() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            422,
            {
                "error": {
                    "code": "UnsupportedMediaType",
                    "message": "media type is not supported",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionMediaError) as exc_info:
        client.call_video("https://example.invalid/v.mp4", "task", "quick")
    assert exc_info.value.status_code == 422
    assert exc_info.value.error_code == "UnsupportedMediaType"


def test_non_media_400_is_not_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {
                "error": {
                    "code": "InvalidParameter",
                    "message": "image parameter is required",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_image(b"bytes", "task", "quick")
    assert not isinstance(exc_info.value, VisionMediaError)
    assert exc_info.value.status_code == 400


def test_non_media_400_video_parameter_is_not_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {
                "error": {
                    "code": "InvalidParameter",
                    "message": "video parameter is required",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_video("https://example.invalid/v.mp4", "task", "quick")
    assert not isinstance(exc_info.value, VisionMediaError)
    assert exc_info.value.status_code == 400


def test_generic_failed_to_parse_is_not_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            422,
            {
                "error": {
                    "code": "InvalidRequest",
                    "message": "failed to parse request body",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_image(b"bytes", "task", "quick")
    assert not isinstance(exc_info.value, VisionMediaError)
    assert exc_info.value.status_code == 422


def test_generic_chinese_unsupported_is_not_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {
                "error": {
                    "code": "InvalidParameter",
                    "message": "模型不支持该请求",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_image(b"bytes", "task", "quick")
    assert not isinstance(exc_info.value, VisionMediaError)
    assert exc_info.value.status_code == 400


def test_generic_invalid_base64_parameter_is_not_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {
                "error": {
                    "code": "InvalidBase64Parameter",
                    "message": "invalid base64 parameter",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_image(b"bytes", "task", "quick")
    assert not isinstance(exc_info.value, VisionMediaError)
    assert exc_info.value.status_code == 400


def test_invalid_base64_image_phrase_is_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {
                "error": {
                    "code": "InvalidParameter",
                    "message": "invalid base64 image data",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionMediaError):
        client.call_image(b"bytes", "task", "quick")


def test_failed_to_parse_video_phrase_is_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            422,
            {
                "error": {
                    "code": "InvalidRequest",
                    "message": "failed to parse video frame",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionMediaError):
        client.call_video("https://example.invalid/v.mp4", "task", "quick")


def test_chinese_unsupported_media_phrase_is_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            400,
            {
                "error": {
                    "code": "InvalidParameter",
                    "message": "不支持的媒体类型",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionMediaError):
        client.call_video("https://example.invalid/v.mp4", "task", "quick")


def test_non_media_404_is_not_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            404,
            {
                "error": {
                    "code": "ModelNotFound",
                    "message": "model not found",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_image(b"bytes", "task", "quick")
    assert not isinstance(exc_info.value, VisionMediaError)
    assert exc_info.value.status_code == 404


def test_non_media_422_is_not_vision_media_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            422,
            {
                "error": {
                    "code": "InvalidRequest",
                    "message": "request configuration invalid",
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(ReadImageError) as exc_info:
        client.call_image(b"bytes", "task", "quick")
    assert not isinstance(exc_info.value, VisionMediaError)
    assert exc_info.value.status_code == 422


def test_api_errors_do_not_expose_key_or_media_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("READ_IMAGE_API_KEY", "fake-doubao-key")
    monkeypatch.setattr(http.logger, "propagate", True)
    secret_body = "SECRET_MEDIA_BODY"

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            500,
            {
                "error": {
                    "message": (f"{api.api_key()} {secret_body} data:image/png;base64,ZmFrZQ==")
                }
            },
        )

    client, _ = _client_with_handler(handler)
    with caplog.at_level(logging.WARNING, logger="read-image-http"):
        with pytest.raises(ReadImageError) as exc_info:
            client.call_image(b"ZmFrZQ==", "task", "quick")
    combined = f"{str(exc_info.value)}\n{caplog.text}"
    assert secret_body not in combined
    assert "ZmFrZQ==" not in combined
    assert "fake-doubao-key" not in combined.lower()
    assert "data:image/png;base64,ZmFrZQ==" not in combined
    assert caplog.text
    assert exc_info.value.status_code == 500


def test_rate_limit_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "MAX_RATE_LIMIT_RETRIES", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            429,
            {"error": {"code": "RateLimit", "message": "slow"}},
            headers={"Retry-After": "3"},
        )

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionRateLimitError) as exc_info:
        client.call_image(b"bytes", "task", "quick")
    assert exc_info.value.status_code == 429
    assert exc_info.value.error_code == "RateLimit"
    assert exc_info.value.retry_after == "3"


def test_timeout_error_keeps_structured_class() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client, _ = _client_with_handler(handler)
    with pytest.raises(VisionTimeoutError):
        client.call_image(b"bytes", "task", "quick")
