from __future__ import annotations

import pytest

from omnimodal.errors import WindowsCaptureError
from omnimodal.mcp.windows_capture_server import (
    _CAPTURE_PS,
    _clean_powershell_error,
    _require_windows,
    _safe_filename,
    capture_windows,
)


def test_safe_filename() -> None:
    assert _safe_filename('Chrome - "My Page"') == "Chrome_-__My_Page"
    assert _safe_filename("...") == "window"


def test_require_windows_friendly_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omnimodal.mcp.windows_capture_server.os.name", "posix")
    with pytest.raises(WindowsCaptureError):
        _require_windows()


def test_require_windows_ok_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omnimodal.mcp.windows_capture_server.os.name", "nt")
    _require_windows()


def test_capture_windows_invalid_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omnimodal.mcp.windows_capture_server.os.name", "nt")
    with pytest.raises(WindowsCaptureError):
        capture_windows(mode="bogus")


def test_capture_windows_window_mode_requires_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omnimodal.mcp.windows_capture_server.os.name", "nt")
    with pytest.raises(WindowsCaptureError):
        capture_windows(mode="window", window=None)


def test_capture_script_has_blank_fallback() -> None:
    assert "Test-BlankBitmap" in _CAPTURE_PS
    assert "CopyFromScreen" in _CAPTURE_PS


def test_capture_script_has_debug_markers_and_configurable_step() -> None:
    assert "[CAPTURE-DEBUG]" in _CAPTURE_PS
    assert "WINDOWS_CAPTURE_SAMPLE_STEP" in _CAPTURE_PS
    assert "GetWindowRect failed" in _CAPTURE_PS


def test_clean_powershell_error_extracts_error_and_keeps_debug_lines() -> None:
    raw = (
        '<S S="Warning">[CAPTURE-DEBUG] PrintWindow flag=2 ok=False</S>'
        '<S S="Error">Bitmap.Save failed: C:\\tmp\\a.png : access denied</S>'
    )
    cleaned = _clean_powershell_error(raw)
    assert "Bitmap.Save failed" in cleaned
    assert "[CAPTURE-DEBUG] PrintWindow flag=2 ok=False" in cleaned


def test_clean_powershell_error_still_replaces_clixml_newlines() -> None:
    assert _clean_powershell_error("line1_x000D__x000A_line2") == "line1 line2"
