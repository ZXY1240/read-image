from __future__ import annotations

import os


def language() -> str:
    return os.environ.get("READ_IMAGE_LANGUAGE", "zh").strip().lower() or "zh"


def tr(zh: str, en: str) -> str:
    return en if language() == "en" else zh


class ReadImageError(RuntimeError):
    """Raised when image/video processing or the vision API fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        error_code: str | None = None,
        detail: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.detail = detail


class VisionTimeoutError(ReadImageError):
    """Raised when the vision API does not respond within the profile timeout."""


class VisionRateLimitError(ReadImageError):
    """Raised when the vision API returns HTTP 429."""

    def __init__(
        self,
        message: str,
        retry_after: str | None = None,
        error_code: str | None = None,
        detail: str | None = None,
    ):
        super().__init__(
            message,
            status_code=429,
            error_code=error_code,
            detail=detail,
        )
        self.retry_after = retry_after


class VisionMediaError(ReadImageError):
    """Raised when the vision API rejects media content or media processing fails."""


class CapturePageError(RuntimeError):
    """Raised when browser capture or interaction fails."""


class WindowsCaptureError(RuntimeError):
    """Raised when Windows screenshot or window enumeration fails."""
