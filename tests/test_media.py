from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from read_image import api
from read_image.config import DEFAULT_IMAGE_FORMAT, DEFAULT_MAX_DIMENSION, image_format
from read_image.errors import ReadImageError, VisionMediaError
from read_image.media import (
    MAX_VIDEO_CONVERSION_DEPTH,
    _analyze_local_video,
    _analyze_local_video_files,
    _analyze_remote_video,
    _compress_video_to_limit,
    _download_video_url,
    _file_data_url,
    _is_video_media_error,
    _remote_size,
    _video_mime,
    _video_too_large_error,
    prepare_image,
    video_base64_max_bytes,
    video_download_max_bytes,
    video_max_bytes,
)


def _mock_http_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> httpx.Client:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(api, "http_client", client)
    return client


def _mock_video_api(monkeypatch: pytest.MonkeyPatch, handler: Any) -> httpx.Client:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(api, "http_client", client)
    monkeypatch.setattr(api, "default_client", api.VisionClient(client=client))
    monkeypatch.setattr("read_image.media.validate_remote_url", lambda url: url)
    return client


def _json_response(
    status_code: int,
    payload: dict[str, Any],
) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _api_path(request: httpx.Request) -> str:
    return request.url.path.removeprefix("/api/v3")


def test_prepare_image_returns_bytes_and_mime_tuple(tmp_path: Path) -> None:
    image = Image.new("RGB", (20, 10), "red")
    path = tmp_path / "sample.png"
    image.save(path)
    data, mime = prepare_image(str(path))
    assert isinstance(data, bytes)
    assert mime == "image/png"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_image_format_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("READ_IMAGE_FORMAT", raising=False)
    assert DEFAULT_IMAGE_FORMAT == "auto"
    assert DEFAULT_MAX_DIMENSION == 2048
    assert image_format() == "auto"


def test_prepare_image_preserves_png_by_default(tmp_path: Path) -> None:
    image = Image.new("RGB", (20, 10), "red")
    path = tmp_path / "sample.png"
    image.save(path)
    data, mime = prepare_image(str(path))
    assert mime == "image/png"
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "PNG"


def test_prepare_image_jpeg_photo_remains_jpeg(tmp_path: Path) -> None:
    image = Image.new("RGB", (20, 10), "red")
    path = tmp_path / "photo.jpg"
    image.save(path, format="JPEG")
    data, mime = prepare_image(str(path))
    assert mime == "image/jpeg"
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "JPEG"


def test_prepare_image_explicit_jpeg_policy_returns_jpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("READ_IMAGE_FORMAT", "jpeg")
    image = Image.new("RGB", (20, 10), "red")
    path = tmp_path / "sample.png"
    image.save(path)
    data, mime = prepare_image(str(path))
    assert mime == "image/jpeg"
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "JPEG"


def test_prepare_image_preserves_transparent_png(tmp_path: Path) -> None:
    image = Image.new("RGBA", (20, 10), (255, 0, 0, 0))
    path = tmp_path / "transparent.png"
    image.save(path)
    data, mime = prepare_image(str(path))
    assert mime == "image/png"
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "PNG"
    assert decoded.mode == "RGBA"
    assert decoded.getpixel((0, 0))[3] == 0


def test_prepare_image_preserves_gif(tmp_path: Path) -> None:
    image = Image.new("P", (10, 10))
    image.info["transparency"] = 0
    path = tmp_path / "sample.gif"
    image.save(path, format="GIF")
    data, mime = prepare_image(str(path))
    assert mime == "image/gif"
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "GIF"


def test_prepare_image_animated_gif_uses_png_first_frame(tmp_path: Path) -> None:
    frame_one = Image.new("RGB", (10, 10), "red")
    frame_two = Image.new("RGB", (10, 10), "blue")
    path = tmp_path / "animated.gif"
    frame_one.save(
        path,
        format="GIF",
        save_all=True,
        append_images=[frame_two],
        duration=100,
        loop=0,
    )
    data, mime = prepare_image(str(path))
    assert mime == "image/png"
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "PNG"
    assert decoded.size == (10, 10)


def test_prepare_image_preserves_bmp(tmp_path: Path) -> None:
    image = Image.new("RGB", (10, 10), "blue")
    path = tmp_path / "sample.bmp"
    image.save(path, format="BMP")
    data, mime = prepare_image(str(path))
    assert mime == "image/bmp"
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "BMP"


def test_prepare_image_resizes_to_max_dimension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("READ_IMAGE_MAX_DIMENSION", "32")
    image = Image.new("RGB", (64, 16), "red")
    path = tmp_path / "large.png"
    image.save(path)
    data, mime = prepare_image(str(path))
    assert mime == "image/png"
    decoded = Image.open(io.BytesIO(data))
    assert decoded.size == (32, 8)


def test_prepare_image_missing_file_raises(tmp_path: Path) -> None:
    try:
        prepare_image(str(tmp_path / "missing.png"))
    except ReadImageError:
        pass
    else:
        raise AssertionError("expected ReadImageError")


def test_video_max_bytes_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("READ_VIDEO_MAX_MB", "7")
    assert video_max_bytes() == 7 * 1024 * 1024


def test_video_base64_max_bytes_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("READ_VIDEO_BASE64_MAX_MB", "7")
    assert video_base64_max_bytes() == 7 * 1024 * 1024


def test_compress_video_to_limit_never_writes_over_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "compressed-720.mp4"
    input_path.write_bytes(b"input")
    outputs: list[tuple[Path, Path]] = []

    def fake_transcode(
        input_path_arg: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> None:
        outputs.append((input_path_arg, output_path))
        output_path.write_bytes(b"compressed")

    monkeypatch.setattr("read_image.media._transcode_video", fake_transcode)
    result = _compress_video_to_limit(input_path, tmp_path, max_bytes=1024)
    assert result != input_path
    assert outputs[0][0] == input_path
    assert outputs[0][1] != input_path
    assert result.read_bytes() == b"compressed"


def test_video_mime_fallback() -> None:
    assert _video_mime(Path("file.mp4")) == "video/mp4"
    assert _video_mime(Path("file.unknown")) == "video/mp4"


def test_file_data_url_contains_base64(tmp_path: Path) -> None:
    path = tmp_path / "tiny.mp4"
    path.write_bytes(b"abc")
    url = _file_data_url(path)
    assert url.startswith("data:video/mp4;base64,")
    assert "YWJj" in url


def test_video_media_error_uses_structured_fields() -> None:
    assert _is_video_media_error(
        VisionMediaError("bad", status_code=400, error_code="InvalidParameter")
    )
    assert _is_video_media_error(VisionMediaError("bad", status_code=415))
    assert _is_video_media_error(VisionMediaError("bad", error_code="InvalidBase64VideoUrl"))
    assert not _is_video_media_error(ReadImageError("network failure"))


def test_video_media_error_message_markers_still_work() -> None:
    assert _is_video_media_error(ReadImageError("invalid base64 video_url"))
    assert _is_video_media_error(ReadImageError("unsupported format"))


def test_remote_size_uses_httpx_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        return httpx.Response(200, headers={"Content-Length": "123"})

    _mock_http_client(monkeypatch, handler)
    assert _remote_size("https://example.invalid/v.mp4") == 123
    assert captured["method"] == "HEAD"


def test_download_video_url_uses_httpx_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "downloaded-video"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, content=b"video-bytes")

    _mock_http_client(monkeypatch, handler)
    _download_video_url("https://example.invalid/v.mp4", destination)
    assert destination.read_bytes() == b"video-bytes"
    assert not (tmp_path / "downloaded-video.part").exists()


def test_download_video_url_rejects_advertised_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "downloaded-video"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "5"},
            content=b"12345",
        )

    _mock_http_client(monkeypatch, handler)
    with pytest.raises(ReadImageError):
        _download_video_url(
            "https://example.invalid/v.mp4",
            destination,
            max_bytes=4,
        )
    assert not destination.exists()
    assert not (tmp_path / "downloaded-video.part").exists()


def test_download_video_url_rejects_oversized_stream_without_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "downloaded-video"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(b"12345"))

    _mock_http_client(monkeypatch, handler)
    with pytest.raises(ReadImageError):
        _download_video_url(
            "https://example.invalid/v.mp4",
            destination,
            max_bytes=4,
        )
    assert not destination.exists()
    assert not (tmp_path / "downloaded-video.part").exists()


def test_download_video_url_allows_remote_over_50mb_below_download_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert video_download_max_bytes() >= 60 * 1024 * 1024
    destination = tmp_path / "downloaded-video"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(60 * 1024 * 1024)},
            content=b"video-bytes",
        )

    _mock_http_client(monkeypatch, handler)
    _download_video_url("https://example.invalid/v.mp4", destination)
    assert destination.read_bytes() == b"video-bytes"
    assert not (tmp_path / "downloaded-video.part").exists()


def test_download_video_url_http_error_raises_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "downloaded-video"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    _mock_http_client(monkeypatch, handler)
    with pytest.raises(ReadImageError):
        _download_video_url("https://example.invalid/v.mp4", destination)


def test_local_video_tries_files_api_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"video-bytes")
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append(_api_path(request))
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "object": "file",
                    "bytes": len(b"video-bytes"),
                    "created_at": 1,
                    "filename": video.name,
                    "purpose": "user_data",
                    "status": "processed",
                },
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            video_url = body["messages"][1]["content"][1]["video_url"]
            assert video_url == {"file_id": "file-abc"}
            return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        if request.method == "DELETE" and request.url.path.endswith("/files/file-abc"):
            return httpx.Response(204)
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    assert _analyze_local_video(video, "task", "quick", tmp_path) == "ok"
    assert events == ["/files", "/chat/completions", "/files/file-abc"]


def test_local_video_warns_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"video-bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "processed",
                },
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        if request.method == "DELETE" and request.url.path.endswith("/files/file-abc"):
            return _json_response(
                500,
                {"error": {"message": "delete failed"}},
            )
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    result = _analyze_local_video(video, "task", "quick", tmp_path)
    assert result.startswith("ok")
    assert "清理失败" in result


def test_local_video_falls_back_to_base64_when_files_api_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"video-bytes")
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append(_api_path(request))
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                501,
                {"error": {"code": "NotImplemented", "message": "files api unsupported"}},
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            video_url = body["messages"][1]["content"][1]["video_url"]
            assert "url" in video_url
            assert video_url["url"].startswith("data:video/mp4;base64,")
            return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    assert _analyze_local_video(video, "task", "quick", tmp_path) == "ok"
    assert events == ["/files", "/chat/completions"]


def test_local_video_falls_back_to_base64_when_model_rejects_file_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"video-bytes")
    chat_calls = 0
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_calls
        events.append(_api_path(request))
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "object": "file",
                    "bytes": len(b"video-bytes"),
                    "created_at": 1,
                    "filename": video.name,
                    "purpose": "user_data",
                    "status": "processed",
                },
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            video_url = body["messages"][1]["content"][1]["video_url"]
            if chat_calls == 0:
                chat_calls += 1
                assert "file_id" in video_url
                return _json_response(
                    400,
                    {
                        "error": {
                            "code": "InvalidParameter",
                            "message": "file_id not supported",
                        }
                    },
                )
            chat_calls += 1
            assert "url" in video_url
            assert video_url["url"].startswith("data:video/mp4;base64,")
            return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        if request.method == "DELETE" and request.url.path.endswith("/files/file-abc"):
            return httpx.Response(204)
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    assert _analyze_local_video(video, "task", "quick", tmp_path) == "ok"
    assert events == [
        "/files",
        "/chat/completions",
        "/files/file-abc",
        "/chat/completions",
    ]


def test_local_video_cleans_up_file_on_generic_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"video-bytes")
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append(_api_path(request))
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "processed",
                },
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            return _json_response(
                400,
                {
                    "error": {
                        "code": "InvalidRequest",
                        "message": "model not supported",
                    }
                },
            )
        if request.method == "DELETE" and request.url.path.endswith("/files/file-abc"):
            return httpx.Response(204)
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    with pytest.raises(ReadImageError):
        _analyze_local_video(video, "task", "quick", tmp_path)
    assert events == ["/files", "/chat/completions", "/files/file-abc"]


def test_local_video_media_error_retries_converted_file_without_base64_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"video-bytes")
    upload_calls = 0
    chat_calls = 0
    events: list[str] = []

    def fake_convert(input_path: Path, tmp_dir: Path) -> Path:
        converted = tmp_dir / "converted.mp4"
        converted.write_bytes(b"converted-video")
        return converted

    monkeypatch.setattr("read_image.media._convert_video_to_mp4", fake_convert)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_calls, chat_calls
        events.append(_api_path(request))
        if request.method == "POST" and request.url.path.endswith("/files"):
            upload_calls += 1
            file_id = "file-original" if upload_calls == 1 else "file-converted"
            return _json_response(
                200,
                {
                    "id": file_id,
                    "status": "processed",
                },
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            chat_calls += 1
            body = json.loads(request.content)
            video_url = body["messages"][1]["content"][1]["video_url"]
            assert "file_id" in video_url
            if chat_calls == 1:
                return _json_response(
                    415,
                    {
                        "error": {
                            "code": "UnsupportedMediaType",
                            "message": "video format not supported",
                        }
                    },
                )
            return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    assert _analyze_local_video(video, "task", "quick", tmp_path) == "ok"
    assert events == [
        "/files",
        "/chat/completions",
        "/files",
        "/chat/completions",
        "/files/file-converted",
        "/files/file-original",
    ]


def test_local_video_compresses_to_base64_cap_before_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_VIDEO_BASE64_MAX_MB", "1")
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"x" * (1024 * 1024 + 1))
    captured: dict[str, Any] = {}

    def fake_compress(
        input_path: Path,
        tmp_dir: Path,
        **kwargs: Any,
    ) -> Path:
        captured["input"] = input_path
        compressed = tmp_dir / "base64-compressed.mp4"
        compressed.write_bytes(b"small-video")
        return compressed

    monkeypatch.setattr(
        "read_image.media._compress_video_to_limit",
        fake_compress,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                501,
                {"error": {"code": "NotImplemented", "message": "files api unsupported"}},
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            video_url = body["messages"][1]["content"][1]["video_url"]
            assert video_url["url"].startswith("data:video/mp4;base64,")
            return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    assert _analyze_local_video(video, "task", "quick", tmp_path) == "ok"
    assert captured["input"] == video


def test_local_video_raises_friendly_error_when_base64_cap_cannot_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_VIDEO_BASE64_MAX_MB", "1")
    monkeypatch.setenv("READ_IMAGE_LANGUAGE", "en")
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"x" * (1024 * 1024 + 1))
    compressed = False

    def fake_compress(
        input_path: Path,
        tmp_dir: Path,
        **kwargs: Any,
    ) -> Path:
        nonlocal compressed
        compressed = True
        raise _video_too_large_error(video_base64_max_bytes())

    monkeypatch.setattr(
        "read_image.media._compress_video_to_limit",
        fake_compress,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                501,
                {"error": {"code": "NotImplemented", "message": "files api unsupported"}},
            )
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    with pytest.raises(ReadImageError) as exc_info:
        _analyze_local_video(video, "task", "quick", tmp_path)
    assert compressed
    assert "below 1MB" in str(exc_info.value)


def test_remote_video_direct_url_is_sent_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": "123"})
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            events.append("chat")
            body = json.loads(request.content)
            video_url = body["messages"][1]["content"][1]["video_url"]
            assert video_url == {"url": "https://example.invalid/v.mp4"}
            return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    assert (
        _analyze_remote_video(
            "https://example.invalid/v.mp4",
            "task",
            "quick",
            tmp_path,
        )
        == "ok"
    )
    assert events == ["chat"]


def test_remote_video_downloads_over_50mb_before_local_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"Content-Length": str(60 * 1024 * 1024)},
            )
        if request.method == "GET":
            events.append("download")
            return httpx.Response(200, content=b"video-bytes")
        events.append(_api_path(request))
        if request.method == "POST" and request.url.path.endswith("/files"):
            return _json_response(
                200,
                {
                    "id": "file-abc",
                    "status": "processed",
                },
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            video_url = body["messages"][1]["content"][1]["video_url"]
            assert video_url == {"file_id": "file-abc"}
            return _json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        if request.method == "DELETE" and request.url.path.endswith("/files/file-abc"):
            return httpx.Response(204)
        raise AssertionError(request.method, request.url.path)

    _mock_video_api(monkeypatch, handler)
    assert (
        _analyze_remote_video(
            "https://example.invalid/v.mp4",
            "task",
            "quick",
            tmp_path,
        )
        == "ok"
    )
    assert events == ["download", "/files", "/chat/completions", "/files/file-abc"]


def test_video_conversion_depth_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "tiny.mp4"
    video.write_bytes(b"video-bytes")
    provider = api.default_client.provider
    monkeypatch.setattr(
        provider,
        "upload_video_file",
        lambda path, timeout_sec=None: "file-abc",
    )
    monkeypatch.setattr(
        provider,
        "delete_video_file",
        lambda file_id, timeout_sec=30, retries=2: None,
    )

    def reject(*args: object, **kwargs: object) -> str:
        raise VisionMediaError("bad media", status_code=415)

    monkeypatch.setattr(
        provider,
        "call_video",
        lambda video_url, task, mode, timeout_sec=None, file_id=None, gate=None: reject(),
    )
    with pytest.raises(ReadImageError) as exc_info:
        _analyze_local_video_files(
            video,
            "task",
            "quick",
            tmp_path,
            depth=MAX_VIDEO_CONVERSION_DEPTH,
        )
    assert "多次转换" in str(exc_info.value)
