from __future__ import annotations

import pytest

from read_image.errors import WindowsCaptureError
from read_image.mcp.windows_capture_server import (
    _CAPTURE_PS,
    _require_windows,
    _safe_filename,
    capture_windows,
)


def test_safe_filename() -> None:
    assert _safe_filename('Chrome - "My Page"') == "Chrome_-__My_Page"
    assert _safe_filename("...") == "window"


def test_require_windows_friendly_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("read_image.mcp.windows_capture_server.os.name", "posix")
    with pytest.raises(WindowsCaptureError):
        _require_windows()


def test_require_windows_ok_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("read_image.mcp.windows_capture_server.os.name", "nt")
    _require_windows()


def test_capture_windows_invalid_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("read_image.mcp.windows_capture_server.os.name", "nt")
    with pytest.raises(WindowsCaptureError):
        capture_windows(mode="bogus")


def test_capture_windows_window_mode_requires_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("read_image.mcp.windows_capture_server.os.name", "nt")
    with pytest.raises(WindowsCaptureError):
        capture_windows(mode="window", window=None)


def test_capture_script_has_blank_fallback() -> None:
    assert "Test-BlankBitmap" in _CAPTURE_PS
    assert "CopyFromScreen" in _CAPTURE_PS
