from __future__ import annotations

import pytest


@pytest.fixture
def fake_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("READ_IMAGE_API_KEY", "test-key")
