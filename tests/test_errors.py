from __future__ import annotations

from read_image.errors import (
    CapturePageError,
    PluginError,
    ReadImageError,
    WindowsCaptureError,
    is_media_error,
)


def test_all_plugin_errors_share_base_class() -> None:
    assert issubclass(ReadImageError, PluginError)
    assert issubclass(CapturePageError, PluginError)
    assert issubclass(WindowsCaptureError, PluginError)


def test_media_classifier_uses_status_and_string_fallback() -> None:
    assert is_media_error(415, None, "anything")
    assert is_media_error(None, "UnsupportedMediaType", "detail")
    assert is_media_error(None, None, "invalid base64 video_url")
    assert not is_media_error(400, "InvalidParameter", "model not supported")
