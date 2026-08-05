"""Tests for the generation pipeline (submit/poll/download) and cost logic."""

from __future__ import annotations

import json

import pytest

from omnimodal.errors import ReadImageError
from omnimodal.generation import (
    DEFAULT_MAX_VIDEO_DURATION,
    GenerationClient,
    GenerationSpec,
    GenerationTimeoutError,
    max_video_duration,
)


class FakeClient:
    """Minimal stand-in for http_client with scripted responses."""

    def __init__(self):
        self.posts: list[dict] = []
        self.gets: list[dict] = []
        self.responses: dict[str, list] = {}
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


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_submit_returns_task_id(monkeypatch):
    fake = FakeClient()
    fake.responses["post"] = FakeResponse(data={"output": {"task_id": "t-123"}})
    monkeypatch.setattr("omnimodal.generation.http_client", fake)

    spec = GenerationSpec(endpoint="https://example.com/t2i", model="wanx-turbo")
    client = GenerationClient(spec)
    task_id = client.submit({"model": "wanx-turbo"})
    assert task_id == "t-123"
    # must carry X-DashScope-Async header
    assert fake.posts[0]["headers"].get("X-DashScope-Async") == "enable"


def test_submit_missing_task_id_raises(monkeypatch):
    fake = FakeClient()
    fake.responses["post"] = FakeResponse(data={"output": {}})
    monkeypatch.setattr("omnimodal.generation.http_client", fake)

    spec = GenerationSpec(endpoint="https://example.com", model="m")
    with pytest.raises(ReadImageError):
        GenerationClient(spec).submit({})


def test_wait_for_result_success(monkeypatch):
    fake = FakeClient()
    pending = FakeResponse(data={"output": {"task_status": "PENDING"}})
    done = FakeResponse(
        data={
            "output": {
                "task_status": "SUCCEEDED",
                "results": [{"url": "https://cdn.example.com/img.png"}],
            }
        }
    )
    fake.responses["get"] = [pending, done]
    monkeypatch.setattr("omnimodal.generation.http_client", fake)

    spec = GenerationSpec(endpoint="https://example.com", model="m", poll_interval=0)
    client = GenerationClient(spec)
    data = client.wait_for_result("t-1")
    assert data["output"]["task_status"] == "SUCCEEDED"
    assert fake.gets[0]["url"].endswith("/tasks/t-1")


def test_wait_for_result_failed_raises(monkeypatch):
    fake = FakeClient()
    fake.responses["get"] = FakeResponse(
        data={
            "output": {
                "task_status": "FAILED",
                "code": "InvalidParameter",
                "message": "resolution must be 720P or 1080P",
            }
        }
    )
    monkeypatch.setattr("omnimodal.generation.http_client", fake)

    spec = GenerationSpec(endpoint="https://example.com", model="m", poll_interval=0)
    with pytest.raises(Exception, match="InvalidParameter.*720P or 1080P"):
        GenerationClient(spec).wait_for_result("t-1")


def test_wait_for_result_timeout_raises_generation_timeout(monkeypatch):
    fake = FakeClient()
    fake.responses["get"] = FakeResponse(data={"output": {"task_status": "PENDING"}})
    monkeypatch.setattr("omnimodal.generation.http_client", fake)

    spec = GenerationSpec(endpoint="https://example.com", model="m", poll_interval=0, timeout_sec=0)
    with pytest.raises(GenerationTimeoutError) as excinfo:
        GenerationClient(spec).wait_for_result("t-1")
    assert excinfo.value.task_id == "t-1"


def test_max_video_duration_default():
    assert max_video_duration() == DEFAULT_MAX_VIDEO_DURATION
