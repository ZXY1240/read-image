"""Tests for audio understanding and transcription (audio_processing.py).

Covers the input_audio content construction (base64 vs URL branches), the
paraformer async task flow, and error paths. Uses a FakeClient stand-in for
http_client, matching tests/test_generation.py style.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from omnimodal import audio_processing
from omnimodal.errors import ReadImageError

pytestmark = pytest.mark.usefixtures("fake_api_key")


class FakeStream:
    """SSE 流式响应的最小 stand-in（analyze_audio 走 http_client.stream）。"""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._chunks)

    def read(self) -> bytes:
        return b"".join(self._chunks)

    def iter_lines(self):
        text = b"".join(self._chunks).decode("utf-8", errors="replace")
        return iter(text.splitlines())


def _sse(data: dict) -> list[bytes]:
    import json as _json

    return [f"data: {_json.dumps(data, ensure_ascii=False)}\n\n".encode()]


class FakeClient:
    def __init__(self):
        self.posts: list[dict] = []
        self.gets: list[dict] = []
        self.streams: list[dict] = []
        self.responses: dict[str, object] = {}
        self._get_index = 0

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self.responses.get("post")

    def get(self, url, headers=None, timeout=None, **kwargs):
        self.gets.append({"url": url})
        resp = self.responses.get("get")
        if isinstance(resp, list):
            idx = min(self._get_index, len(resp) - 1)
            self._get_index += 1
            return resp[idx]
        return resp

    def stream(self, method, url, headers=None, json=None, timeout=None, **kwargs):
        self.streams.append({"method": method, "url": url, "json": json})
        return self.responses.get("stream")


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    fake = FakeClient()
    # analyze_audio 直接用 audio_processing.http_client；
    # transcribe/asr 走 GenerationClient（omnimodal.generation.http_client）
    monkeypatch.setattr("omnimodal.audio_processing.http_client", fake)
    monkeypatch.setattr("omnimodal.generation.http_client", fake)
    # 上传流程已在 test_upload.py 单独覆盖，这里 mock 掉
    monkeypatch.setattr(
        "omnimodal.audio_processing.get_temporary_url",
        lambda path, model, content_type: "oss://dashscope-instant/test.mp3",
    )
    return fake


# ---- _audio_content_item 构造 ----


def test_audio_content_item_http_url_passes_url() -> None:
    item = audio_processing._audio_content_item(
        "https://example.com/a.mp3", audio_processing.OMNI_MODEL
    )
    assert item == {
        "type": "input_audio",
        "input_audio": {
            "data": "https://example.com/a.mp3",
            "format": "mp3",
        },
    }


def test_audio_content_item_oss_url_passes_through(tmp_path) -> None:
    # oss:// 或未知形式按 URL 透传，不再误包 base64
    item = audio_processing._audio_content_item(
        "oss://dashscope-instant/x/a.mp3", audio_processing.OMNI_MODEL
    )
    assert item == {
        "type": "input_audio",
        "input_audio": {
            "data": "oss://dashscope-instant/x/a.mp3",
            "format": "mp3",
        },
    }


def test_audio_content_item_small_local_file_uses_base64(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"\xff\xf3tiny-audio")
    # 小文件直接 base64，不走上传
    uploaded: list[str] = []
    monkeypatch.setattr(
        "omnimodal.audio_processing.get_temporary_url",
        lambda path, model, content_type: uploaded.append(path) or "oss://NOPE",
    )
    item = audio_processing._audio_content_item(str(audio), audio_processing.OMNI_MODEL)
    assert item["type"] == "input_audio"
    data = item["input_audio"]["data"]
    assert item["input_audio"]["format"] == "mp3"
    import base64

    expected_data = base64.b64encode(b"\xff\xf3tiny-audio").decode("ascii")
    assert data == f"data:audio/mpeg;base64,{expected_data}"
    assert uploaded == []  # 未触发上传


def test_audio_content_item_large_local_file_uploads(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "big.mp3"
    # 构造超过 base64 上限的文件（10MB base64 → 约 7.5MB 原始数据）
    audio.write_bytes(b"x" * (8 * 1024 * 1024))
    uploaded: list[str] = []
    monkeypatch.setattr(
        "omnimodal.audio_processing.get_temporary_url",
        lambda path, model, content_type: (
            uploaded.append(path) or "oss://dashscope-instant/uploaded/big.mp3"
        ),
    )
    item = audio_processing._audio_content_item(str(audio), audio_processing.OMNI_MODEL)
    assert uploaded == [str(audio)]
    assert item == {
        "type": "input_audio",
        "input_audio": {
            "data": "oss://dashscope-instant/uploaded/big.mp3",
            "format": "mp3",
        },
    }


# ---- analyze_audio payload ----


def test_analyze_audio_builds_payload_with_base64(fake: FakeClient, tmp_path) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"tiny")
    fake.responses["stream"] = FakeStream(
        _sse({"choices": [{"delta": {"content": "音频里说：你好"}}]})
    )
    result = audio_processing.analyze_audio(
        str(audio), task="说了什么", mode="standard", tier="standard"
    )
    assert "你好" in result
    body = fake.streams[0]["json"]
    assert body["model"] == "qwen3.5-omni-flash"
    assert body["stream"] is True
    content = body["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "说了什么"}
    assert content[1]["type"] == "input_audio"
    assert content[1]["input_audio"]["format"] == "mp3"
    import base64

    assert content[1]["input_audio"]["data"] == (
        f"data:audio/mpeg;base64,{base64.b64encode(b'tiny').decode('ascii')}"
    )


def test_analyze_audio_pro_tier_uses_omni_plus(fake: FakeClient, tmp_path) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"tiny")
    fake.responses["stream"] = FakeStream(_sse({"choices": [{"delta": {"content": "ok"}}]}))
    audio_processing.analyze_audio(str(audio), tier="pro")
    assert fake.streams[0]["json"]["model"] == "qwen3.5-omni-plus"


# ---- transcribe_audio (paraformer async) ----


def test_transcribe_wait_false_returns_task_id(fake: FakeClient, tmp_path) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"tiny")
    fake.responses["post"] = FakeResponse(
        data={"output": {"task_id": "asr-1", "task_status": "PENDING"}}
    )
    result = audio_processing.transcribe_audio(str(audio), wait=False)
    assert result == {"task_id": "asr-1", "status": "PENDING"}


def test_transcribe_waits_and_returns_text(fake: FakeClient, tmp_path) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"tiny")
    fake.responses["post"] = FakeResponse(
        data={"output": {"task_id": "asr-1", "task_status": "PENDING"}}
    )
    fake.responses["get"] = FakeResponse(
        data={
            "output": {
                "task_status": "SUCCEEDED",
                "results": [{"text": "第一句"}, {"text": "第二句"}],
            }
        }
    )
    result = audio_processing.transcribe_audio(str(audio))
    assert result["status"] == "SUCCEEDED"
    assert result["text"] == "第一句\n第二句"


def test_transcribe_succeeded_but_empty_results_raises(fake: FakeClient, tmp_path) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"tiny")
    fake.responses["post"] = FakeResponse(
        data={"output": {"task_id": "asr-1", "task_status": "PENDING"}}
    )
    fake.responses["get"] = FakeResponse(
        data={"output": {"task_status": "SUCCEEDED", "results": []}}
    )
    with pytest.raises(ReadImageError):
        audio_processing.transcribe_audio(str(audio))


def test_asr_task_status_extracts_text(fake: FakeClient) -> None:
    fake.responses["get"] = FakeResponse(
        data={
            "output": {
                "task_status": "SUCCEEDED",
                "results": [{"text": "你好"}],
            }
        }
    )
    result = audio_processing.asr_task_status("asr-1")
    assert result == {"task_id": "asr-1", "status": "SUCCEEDED", "text": "你好"}


def test_asr_task_status_pending(fake: FakeClient) -> None:
    fake.responses["get"] = FakeResponse(data={"output": {"task_status": "RUNNING"}})
    result = audio_processing.asr_task_status("asr-1")
    assert result == {"task_id": "asr-1", "status": "RUNNING"}


def test_audio_should_transcribe_long_audio() -> None:
    assert audio_processing.audio_should_transcribe("long.mp3", duration_sec=600) is True
    assert audio_processing.audio_should_transcribe("short.mp3", duration_sec=30) is False


def test_transcribe_openai_like_audio_multipart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    monkeypatch.setenv("OMNIMODAL_BASE_URL", "https://api.z.ai/api/paas/v4")
    monkeypatch.setenv("OMNIMODAL_ASR_MODEL", "glm-asr-2512")
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"tiny")
    captured: dict = {}

    class FakePostClient:
        def post(self, url, headers=None, data=None, files=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            captured["files"] = files
            return FakeResponse(200, {"text": "你好"})

    monkeypatch.setattr(audio_processing, "http_client", FakePostClient())
    result = audio_processing._transcribe_openai_like_audio(str(audio))
    assert result == {"text": "你好", "status": "SUCCEEDED"}
    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["data"]["model"] == "glm-asr-2512"
    assert "file" in captured["files"]


def test_transcribe_audio_routes_zai(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"tiny")
    called: dict = {}

    def fake_transcribe(path_or_url, language="zh", model=None):
        called["path"] = path_or_url
        called["model"] = model
        return {"text": "ok", "status": "SUCCEEDED"}

    monkeypatch.setattr(
        audio_processing,
        "_transcribe_openai_like_audio",
        fake_transcribe,
    )
    result = audio_processing.transcribe_audio(str(audio))
    assert result["text"] == "ok"
    assert called["model"] == "glm-asr-2512"


def test_recognize_audio_routes_non_dashscope_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnimodal import api

    monkeypatch.setenv("OMNIMODAL_PROVIDER", "openai_compatible")

    class FakeProvider:
        def call_audio(self, audio_url, task, mode):
            return "audio-ok"

    monkeypatch.setattr(
        api,
        "default_client",
        SimpleNamespace(provider=FakeProvider()),
    )
    assert audio_processing.recognize_audio("https://example.invalid/a.mp3") == "audio-ok"
