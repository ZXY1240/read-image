from __future__ import annotations

import pytest


@pytest.fixture
def fake_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIMODAL_API_KEY", "test-key")
    monkeypatch.setenv(
        "OMNIMODAL_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    monkeypatch.setenv("OMNIMODAL_IMAGE_MODEL", "qwen3.7-flash")
