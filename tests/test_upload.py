"""Tests for the DashScope temporary upload flow (upload.py).

Covers getPolicy -> multipart POST -> oss:// URL, error paths, and
file-handle hygiene (the open() handle is managed by a with block).
"""

from __future__ import annotations

import json

import pytest

from omnimodal import upload
from omnimodal.errors import ReadImageError

_UPLOAD_POLICY = {
    "data": {
        "upload_host": "https://dashscope-instant.oss-cn-beijing.aliyuncs.com",
        "policy": "pol-xxx",
        "signature": "sig-xxx",
        "oss_access_key_id": "key-xxx",
        "upload_dir": "uploads/2026/08/05",
        "x_oss_object_acl": "private",
        "x_oss_forbid_overwrite": "true",
        "expire_in_seconds": 3600,
    }
}


class FakeClient:
    def __init__(self):
        self.gets: list[dict] = []
        self.posts: list[dict] = []
        self.responses: dict[str, object] = {}

    def get(
        self,
        url,
        params=None,
        headers=None,
        timeout=None,
        **kwargs,
    ):
        self.gets.append({"url": url, "params": params})
        return self.responses.get("get")

    def post(
        self,
        url,
        params=None,
        headers=None,
        json=None,
        data=None,
        files=None,
        timeout=None,
        **kwargs,
    ):
        self.posts.append(
            {"url": url, "params": params, "json": json, "data": data, "files": files}
        )
        return self.responses.get("post")


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
    monkeypatch.setattr("omnimodal.upload.http_client", fake)
    return fake


def test_get_temporary_url_full_flow(fake: FakeClient, tmp_path) -> None:
    file_ = tmp_path / "sample.mp3"
    file_.write_bytes(b"audio-data")

    fake.responses["get"] = FakeResponse(data=_UPLOAD_POLICY)
    fake.responses["post"] = FakeResponse()

    result = upload.get_temporary_url(str(file_), "paraformer-v2", "audio/mpeg")

    assert result == "oss://uploads/2026/08/05/sample.mp3"
    # getPolicy 一次 + 上传一次
    assert len(fake.gets) == 1
    assert len(fake.posts) == 1
    policy_req = fake.gets[0]
    assert policy_req["params"] == {"action": "getPolicy", "model": "paraformer-v2"}
    upload_req = fake.posts[0]
    assert upload_req["data"]["policy"] == "pol-xxx"
    assert upload_req["data"]["Signature"] == "sig-xxx"
    assert upload_req["data"]["OSSAccessKeyId"] == "key-xxx"
    assert upload_req["data"]["x-oss-object-acl"] == "private"
    assert upload_req["data"]["x-oss-forbid-overwrite"] == "true"
    assert upload_req["data"]["success_action_status"] == "200"
    assert upload_req["url"] == "https://dashscope-instant.oss-cn-beijing.aliyuncs.com"
    # 文件句柄在请求完成后已关闭（无 with 泄漏时文件可正常读写删除）
    assert file_.is_file()
    file_.unlink()  # 不报 WindowsError 即句柄未占用


def test_get_temporary_url_file_missing(fake: FakeClient, tmp_path) -> None:
    with pytest.raises(ReadImageError):
        upload.get_temporary_url(str(tmp_path / "nope.mp3"), "paraformer-v2")
    assert fake.gets == []
    assert fake.posts == []


def test_get_temporary_url_policy_missing_upload_host(fake: FakeClient, tmp_path) -> None:
    file_ = tmp_path / "a.mp3"
    file_.write_bytes(b"x")
    fake.responses["get"] = FakeResponse(data={"data": {"policy": "p"}})
    with pytest.raises(ReadImageError):
        upload.get_temporary_url(str(file_), "paraformer-v2")


def test_get_temporary_url_upload_http_error(fake: FakeClient, tmp_path) -> None:
    file_ = tmp_path / "a.mp3"
    file_.write_bytes(b"x")

    class Seq:
        def __init__(self):
            self.n = 0

        def get(self, url, **kwargs):
            return FakeResponse(data=_UPLOAD_POLICY)

        def post(self, url, **kwargs):
            return FakeResponse(status_code=403, data={"Error": {"Message": "denied"}})

    seq = Seq()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("omnimodal.upload.http_client", seq)
    try:
        with pytest.raises(ReadImageError):
            upload.get_temporary_url(str(file_), "paraformer-v2")
    finally:
        monkeypatch.undo()
