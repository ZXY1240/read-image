"""Tests for generation_server: tier -> model/price mapping, cost labels,
and get_generation_result status handling.

The price assertions here are the guard against future drift between the
Field descriptions, _*_spec() prices, and README.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from omnimodal.errors import ReadImageError
from omnimodal.mcp import generation_server as gs


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data


class FakeGenerationHttp:
    def __init__(self):
        self.posts: list[dict] = []
        self.gets: list[dict] = []
        self.responses: dict[str, object] = {}

    def post(self, url, headers=None, json=None, data=None, files=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers or {}})
        return self.responses.get("post")

    def get(self, url, headers=None, timeout=None):
        self.gets.append({"url": url})
        return self.responses.get("get")


# ---- tier -> spec 映射与价格 ----


def test_t2i_spec_standard_uses_qwen_image_30() -> None:
    spec = gs._t2i_spec("standard")
    assert spec.model == "qwen-image-3.0"
    assert spec.price_hint == 0.18
    # 实测确认两个模型都走 multimodal-generation（Chat、同步）
    assert spec.endpoint == gs.T2I_CHAT_ENDPOINT


def test_t2i_spec_pro_uses_wan27_image_pro() -> None:
    spec = gs._t2i_spec("pro")
    assert spec.model == "wan2.7-image-pro"
    assert spec.price_hint == 0.50
    assert spec.endpoint == gs.T2I_CHAT_ENDPOINT


def test_generate_image_requires_confirmation() -> None:
    result = asyncio.run(
        gs.generate_image(
            "a cat",
            tier="standard",
            size="1024*1024",
            n=1,
            confirm=False,
        )
    )
    assert result["status"] == "NEEDS_CONFIRMATION"
    assert "confirm=true" in result["note"]


def test_generate_image_chat_payload_uses_input_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """payload 必须用 input.messages（真实调用确认），不是顶层 messages。"""
    captured: dict = {}

    class FakeResp:
        status_code = 200

        def __init__(self):
            pass

        def json(self):
            return {
                "output": {
                    "choices": [
                        {"message": {"content": [{"image": "https://x/img.png", "type": "image"}]}}
                    ]
                }
            }

    def fake_post(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return FakeResp()

    fake_client = type("FakeHttp", (), {"post": fake_post})()
    monkeypatch.setattr("omnimodal.mcp.generation_server.http_client", fake_client)
    monkeypatch.setattr(
        "omnimodal.mcp.generation_server._output_dir",
        lambda: Path(tempfile.gettempdir()),
    )
    monkeypatch.setattr(
        "omnimodal.mcp.generation_server.GenerationClient.download_result",
        lambda self, url, name: Path(tempfile.gettempdir()) / name,
    )

    asyncio.run(
        gs.generate_image(
            "a cat",
            tier="standard",
            size="1024*1024",
            n=1,
            confirm=True,
        )
    )

    body = captured["json"]
    assert "input" in body and "messages" not in body
    assert body["input"]["messages"][0]["content"] == [{"text": "a cat"}]


def test_t2v_spec_tiers() -> None:
    standard = gs._t2v_spec("standard")
    assert standard.model == "wan2.7-t2v"
    assert standard.price_hint == 0.60
    pro = gs._t2v_spec("pro")
    assert pro.model == "wan2.7-t2v"
    assert pro.price_hint == 1.00
    max_ = gs._t2v_spec("max")
    assert max_.model == "happyhorse-1.1-t2v"
    assert max_.price_hint == 1.20


def test_tts_spec_tiers() -> None:
    standard = gs._tts_spec("standard")
    assert standard.model == "qwen3-tts-instruct-flash"
    assert standard.price_hint == 0.80
    pro = gs._tts_spec("pro")
    assert pro.model == "cosyvoice-v3.5-plus"
    assert pro.price_hint == 1.50


def test_field_descriptions_match_spec_prices() -> None:
    """Field description 里的价格必须与 _*_spec() 一致，防止再次漂移。"""
    import inspect

    source = inspect.getsource(gs)
    # 文生图
    assert "standard(qwen-image-3.0)" in source
    assert "pro(wan2.7-image-pro)" in source
    # 文生视频
    assert "standard(wan2.7-t2v)" in source
    assert "pro(wan2.7-t2v 1080P)" in source
    assert "max(happyhorse-1.1-t2v)" in source
    # 图生视频
    assert "standard(wan2.7-i2v)" in source
    # TTS
    assert "standard/pro/max，音频生成专用档" in source


def test_t2i_spec_zai_uses_images_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    monkeypatch.delenv("OMNIMODAL_BASE_URL", raising=False)
    spec = gs._t2i_spec("standard")
    assert spec.model == "glm-image"
    assert spec.endpoint == "https://api.z.ai/api/paas/v4/images/generations"


def test_zai_video_spec_uses_async_result_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    monkeypatch.delenv("OMNIMODAL_BASE_URL", raising=False)
    monkeypatch.delenv("OMNIMODAL_VIDEO_GEN_BASE_URL", raising=False)
    spec = gs._zai_video_spec("standard")
    assert spec.model == "cogvideox-3"
    assert spec.use_dashscope_async_header is False
    assert spec.submit_result_path == ("id",)
    assert spec.status_path == ("task_status",)
    assert spec.success_statuses == ("SUCCESS",)
    assert spec.poll_url_template == "https://api.z.ai/api/paas/v4/async-result/{task_id}"


# ---- 费用计算 ----


def test_format_cost_with_price() -> None:
    spec = gs._t2v_spec("standard")
    detail = "生成 5 秒视频"
    out = gs._format_cost(spec, detail)
    assert detail in out
    assert "0.6" in out


def test_default_tier_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIMODAL_DEFAULT_TIER", raising=False)
    assert gs._default_tier() == "standard"
    monkeypatch.setenv("OMNIMODAL_DEFAULT_TIER", "max")
    assert gs._default_tier() == "max"
    monkeypatch.setenv("OMNIMODAL_DEFAULT_TIER", "bogus")
    assert gs._default_tier() == "standard"


def test_generate_image_zai_posts_to_images_generations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    monkeypatch.delenv("OMNIMODAL_BASE_URL", raising=False)
    monkeypatch.setenv("OMNIMODAL_IMAGE_GEN_MODEL_STANDARD", "glm-image")
    fake = FakeGenerationHttp()
    fake.responses["post"] = FakeResponse(
        200,
        {"data": [{"url": "https://cdn.example.invalid/img.png"}]},
    )
    monkeypatch.setattr(gs, "http_client", fake)
    monkeypatch.setattr(gs, "_output_dir", lambda: tmp_path)
    monkeypatch.setattr(
        gs.GenerationClient,
        "download_result",
        lambda self, url, name: tmp_path / name,
    )
    result = asyncio.run(
        gs.generate_image(
            "a cat",
            tier="standard",
            size="1280x1280",
            n=1,
            confirm=True,
        )
    )
    assert result["status"] == "SUCCEEDED"
    assert fake.posts[0]["url"].endswith("/images/generations")
    body = fake.posts[0]["json"]
    assert body["model"] == "glm-image"
    assert body["size"] == "1280x1280"


def test_generate_image_zai_edit_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    with pytest.raises(ReadImageError):
        asyncio.run(
            gs.generate_image(
                "edit",
                image="C:/tmp/input.png",
                confirm=True,
            )
        )


def test_generate_video_openai_compatible_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "openai_compatible")
    with pytest.raises(ReadImageError):
        asyncio.run(
            gs.generate_video(
                "a cat",
                confirm=True,
            )
        )


def test_generate_audio_zai_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    with pytest.raises(ReadImageError):
        asyncio.run(
            gs.generate_speech(
                "hello",
                confirm=True,
            )
        )


def test_submit_video_task_zai_polls_async_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    monkeypatch.delenv("OMNIMODAL_BASE_URL", raising=False)
    monkeypatch.delenv("OMNIMODAL_VIDEO_GEN_BASE_URL", raising=False)
    fake = FakeGenerationHttp()
    fake.responses["post"] = FakeResponse(200, {"id": "zai-task-1"})
    fake.responses["get"] = FakeResponse(
        200,
        {
            "task_status": "SUCCESS",
            "video_result": [{"url": "https://cdn.example.invalid/result.mp4"}],
        },
    )
    monkeypatch.setattr("omnimodal.generation.http_client", fake)
    monkeypatch.setattr(
        gs.GenerationClient,
        "download_result",
        lambda self, url, name: tmp_path / name,
    )
    spec = gs._zai_video_spec("standard")
    result = gs._submit_video_task(
        spec,
        {"model": spec.model, "prompt": "a cat"},
        True,
        None,
        "cost",
    )
    assert result["status"] == "SUCCEEDED"
    assert fake.posts[0]["url"].endswith("/videos/generations")
    assert "X-DashScope-Async" not in fake.posts[0]["headers"]
    assert fake.gets[0]["url"].endswith("/async-result/zai-task-1")


# ---- get_generation_result 状态流转 ----


class FakePollClient:
    def __init__(self, data):
        self._data = data

    def poll_status(self, task_id: str) -> dict:
        return self._data


def _run(result_data: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    fake = FakePollClient(result_data)
    monkeypatch.setattr(gs, "GenerationClient", lambda spec, output_dir=None: fake)
    return asyncio.run(gs.get_generation_result("task-1"))


def test_get_generation_result_succeeded_image(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(
        {"output": {"task_status": "SUCCEEDED", "results": [{"url": "https://x/img.png"}]}},
        monkeypatch,
    )
    assert result["status"] == "SUCCEEDED"
    assert result["result_url"] == "https://x/img.png"


def test_get_generation_result_succeeded_video_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(
        {"output": {"task_status": "SUCCEEDED", "video_url": "https://x/v.mp4"}},
        monkeypatch,
    )
    assert result["status"] == "SUCCEEDED"
    assert result["result_url"] == "https://x/v.mp4"


def test_get_generation_result_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(
        {"output": {"task_status": "FAILED", "message": "content rejected"}},
        monkeypatch,
    )
    assert result["status"] == "FAILED"
    assert "content rejected" in result["error"]


def test_get_generation_result_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run({"output": {"task_status": "RUNNING"}}, monkeypatch)
    assert result == {"task_id": "task-1", "status": "RUNNING"}


def test_get_generation_result_zai_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIMODAL_PROVIDER", "zai")
    monkeypatch.delenv("OMNIMODAL_BASE_URL", raising=False)
    monkeypatch.delenv("OMNIMODAL_VIDEO_GEN_BASE_URL", raising=False)
    result = _run(
        {
            "task_status": "SUCCESS",
            "video_result": [{"url": "https://z.example/v.mp4"}],
        },
        monkeypatch,
    )
    assert result["status"] == "SUCCESS"
    assert result["result_url"] == "https://z.example/v.mp4"


# ---- v3.0.0 audio/video generation fixes ----


def test_music_endpoint_is_dashscope_music_generation() -> None:
    assert gs.MUSIC_ENDPOINT == (
        "https://dashscope.aliyuncs.com/api/v1/services/audio/music/generation"
    )


def test_music_payload_has_no_duration_and_infers_gender() -> None:
    payload = gs._music_payload("女声抒情歌曲")
    assert payload["model"] == "fun-music-v1"
    assert payload["input"]["prompt"] == "女声抒情歌曲"
    assert payload["input"]["gender"] == "female"
    assert "duration" not in payload["input"]


def test_voice_design_uses_customization_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"output": {"voice": "voice-123"}}

    def fake_post(*args, **kwargs):
        captured["url"] = args[1]
        captured["json"] = kwargs["json"]
        return FakeResp()

    fake_client = type("FakeHttp", (), {"post": fake_post})()
    monkeypatch.setattr(gs, "http_client", fake_client)
    voice_id = gs._create_custom_voice(
        "温柔女声",
        gs.QWEN_TTS_VD_MODEL,
        model="qwen-voice-design",
    )
    assert voice_id == "voice-123"
    assert captured["url"] == gs.VOICE_CUSTOMIZATION_ENDPOINT
    body = captured["json"]
    assert body["model"] == "qwen-voice-design"
    assert body["input"]["target_model"] == gs.QWEN_TTS_VD_MODEL
    assert body["input"]["action"] == "create"


def test_voice_clone_payload_uses_audio_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"output": {"voice": "voice-clone-1"}}

    def fake_post(*args, **kwargs):
        captured["json"] = kwargs["json"]
        return FakeResp()

    fake_client = type("FakeHttp", (), {"post": fake_post})()
    monkeypatch.setattr(gs, "http_client", fake_client)
    voice_id = gs._create_custom_voice(
        "",
        gs.QWEN_TTS_VC_MODEL,
        audio="https://example.invalid/sample.mp3",
        model="qwen-voice-enrollment",
    )
    assert voice_id == "voice-clone-1"
    body = captured["json"]
    assert body["model"] == "qwen-voice-enrollment"
    assert body["input"]["audio"] == {"data": "https://example.invalid/sample.mp3"}


def test_submit_audio_sync_downloads_audio_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class FakeResp:
        status_code = 200

        def json(self):
            return {"output": {"audio": {"url": "https://example.invalid/result.mp3"}}}

    class FakeHttp:
        def post(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr(gs, "http_client", FakeHttp())
    monkeypatch.setattr(gs, "_output_dir", lambda: tmp_path)
    monkeypatch.setattr(
        gs,
        "_download",
        lambda client, url: tmp_path / "result.mp3",
    )
    saved = gs._submit_audio_sync(
        gs.MUSIC_ENDPOINT,
        gs._music_payload("轻快民谣"),
        "fun-music-v1",
    )
    assert saved == tmp_path / "result.mp3"


def test_edit_video_requires_confirmation() -> None:
    result = asyncio.run(
        gs.edit_video(
            "video.mp4",
            "转换成黏土风格",
            tier="standard",
            confirm=False,
        )
    )
    assert result["status"] == "NEEDS_CONFIRMATION"


def test_video_resolution_normalizes_480p_to_720p() -> None:
    assert gs._video_resolution("480P") == "720P"
    assert gs._video_resolution("720P") == "720P"
    assert gs._video_resolution("1080P") == "1080P"
