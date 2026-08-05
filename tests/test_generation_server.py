"""Tests for generation_server: tier -> model/price mapping, cost labels,
and get_generation_result status handling.

The price assertions here are the guard against future drift between the
Field descriptions, _*_spec() prices, and README.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from omnimodal.mcp import generation_server as gs


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data) if data is not None else ""


# ---- tier -> spec 映射与价格 ----

def test_t2i_spec_standard_uses_qwen_image_20() -> None:
    spec = gs._t2i_spec("standard")
    assert spec.model == "qwen-image-2.0"
    assert spec.price_hint == 0.20


def test_t2i_spec_pro_uses_wan27_image_pro() -> None:
    spec = gs._t2i_spec("pro")
    assert spec.model == "wan2.7-image-pro"
    assert spec.price_hint == 0.50


def test_t2v_spec_tiers() -> None:
    standard = gs._t2v_spec("standard")
    assert standard.model == "wan2.7-t2v"
    assert standard.price_hint == 0.60
    pro = gs._t2v_spec("pro")
    assert pro.model == "wan2.7-t2v"
    assert pro.price_hint == 1.00
    max_ = gs._t2v_spec("max")
    assert max_.model == "happyhorse-1.1-t2v"
    assert max_.price_hint == 0.72


def test_tts_spec_tiers() -> None:
    standard = gs._tts_spec("standard")
    assert standard.model == "qwen-audio-3.0-tts"
    assert standard.price_hint == 1.00
    pro = gs._tts_spec("pro")
    assert pro.model == "cosyvoice-v3.5-plus"
    assert pro.price_hint == 1.50


def test_field_descriptions_match_spec_prices() -> None:
    """Field description 里的价格必须与 _*_spec() 一致，防止再次漂移。"""
    import inspect

    source = inspect.getsource(gs)
    # 文生图
    assert "standard(qwen-image-2.0 0.2元/张)" in source
    assert "pro(wan2.7-image-pro 0.5元/张)" in source
    # 文生视频
    assert "standard(wan2.7-t2v 0.6元/秒)" in source
    assert "pro(wan2.7-t2v 1元/秒)" in source
    assert "max(happyhorse-1.1-t2v 0.72元/秒)" in source
    # 图生视频
    assert "standard(wan2.7-i2v 0.6元/秒)" in source
    # TTS
    assert "standard(qwen-audio-3.0-tts 1元/万字符)" in source
    assert "pro(cosyvoice-v3.5-plus 1.5元/万字符)" in source


# ---- 费用计算 ----

def test_format_cost_with_price() -> None:
    spec = gs._t2v_spec("standard")
    detail = "生成 5 秒视频"
    out = gs._format_cost(spec, detail)
    assert detail in out
    assert "0.6" in out


def test_default_tier_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("READ_IMAGE_DEFAULT_TIER", raising=False)
    assert gs._default_tier() == "standard"
    monkeypatch.setenv("READ_IMAGE_DEFAULT_TIER", "max")
    assert gs._default_tier() == "max"
    monkeypatch.setenv("READ_IMAGE_DEFAULT_TIER", "bogus")
    assert gs._default_tier() == "standard"


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
