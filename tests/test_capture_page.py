from __future__ import annotations

import pytest

from omnimodal.errors import CapturePageError
from omnimodal.mcp.capture_page_server import (
    _max_full_page_height,
    _normalize_actions,
    _parse_viewport,
    _settle_ms,
    _wait_until,
)


def test_parse_viewport() -> None:
    assert _parse_viewport("1280x800") == (1280, 800)
    assert _parse_viewport(" 1024 X 768 ") == (1024, 768)


def test_parse_viewport_invalid() -> None:
    with pytest.raises(CapturePageError):
        _parse_viewport("wide")


def test_normalize_actions() -> None:
    actions = [
        {"action": "click", "selector": "#a"},
        {"action": "scroll", "amount": 400},
        {"action": "wait", "ms": 250},
        {"action": "type", "selector": "input", "text": "hi", "press": "Enter"},
        {"action": "press", "key": "Escape"},
    ]
    normalized = _normalize_actions(actions)
    assert normalized[0] == {"action": "click", "selector": "#a"}
    assert normalized[1]["amount"] == 400
    assert normalized[3]["press"] == "Enter"
    assert normalized[4]["key"] == "Escape"


def test_normalize_actions_requires_selector() -> None:
    with pytest.raises(CapturePageError):
        _normalize_actions([{"action": "click"}])


def test_normalize_actions_rejects_unknown() -> None:
    with pytest.raises(CapturePageError):
        _normalize_actions([{"action": "drag"}])


def test_normalize_actions_keeps_scroll_selector() -> None:
    normalized = _normalize_actions([{"action": "scroll", "selector": "#panel", "amount": 200}])
    assert normalized == [{"action": "scroll", "selector": "#panel", "amount": 200}]


def test_wait_until_default_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTURE_PAGE_WAIT_UNTIL", raising=False)
    assert _wait_until() == "domcontentloaded"
    monkeypatch.setenv("CAPTURE_PAGE_WAIT_UNTIL", "networkidle")
    assert _wait_until() == "networkidle"
    monkeypatch.setenv("CAPTURE_PAGE_WAIT_UNTIL", "invalid")
    assert _wait_until() == "domcontentloaded"


def test_settle_ms_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTURE_PAGE_SETTLE_MS", raising=False)
    assert _settle_ms() == 500
    monkeypatch.setenv("CAPTURE_PAGE_SETTLE_MS", "1200")
    assert _settle_ms() == 1200
    monkeypatch.setenv("CAPTURE_PAGE_SETTLE_MS", "bad")
    assert _settle_ms() == 500


def test_max_full_page_height_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTURE_PAGE_MAX_FULL_PAGE_HEIGHT", raising=False)
    assert _max_full_page_height() == 12000
    monkeypatch.setenv("CAPTURE_PAGE_MAX_FULL_PAGE_HEIGHT", "8000")
    assert _max_full_page_height() == 8000
