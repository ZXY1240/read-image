from __future__ import annotations

import os
import re

# Structured error codes that are authoritative for media classification
# after normalization (lowercase, underscores/dashes stripped). An exact
# match here short-circuits before any heuristic text matching.
MEDIA_ERROR_CODES: frozenset[str] = frozenset(
    {
        "invalidimage",
        "invalidbase64image",
        "invalidbase64videourl",
        "invalidvideo",
        "invalidmedia",
        "unsupportedmedia",
        "unsupportedmediatype",
        "mediaparseerror",
        "imagedecodeerror",
        "videodecodeerror",
        "badmedia",
        "mediaerror",
        "invalidformat",
        "invalidmediaformat",
        "decodeerror",
    }
)

# Code fragments for a looser substring match on normalized error codes
# that did not hit MEDIA_ERROR_CODES exactly (e.g. codes carrying extra
# prefixes or suffixes such as "InvalidBase64ImageData").
MEDIA_CODE_FRAGMENTS: tuple[str, ...] = (
    "invalidimage",
    "invalidbase64image",
    "invalidbase64video",
    "invalidvideo",
    "unsupportedmedia",
    "mediaparse",
    "imagedecode",
    "videodecode",
    "badmedia",
    "invalidmedia",
    "decodeerror",
)

# English keywords found in API error messages. These are matched at word
# boundaries (e.g. "video file" matches "video file" but not "video files")
# to avoid false positives from longer words containing the keyword.
MEDIA_MARKERS_EN: tuple[str, ...] = (
    "video_url",
    "image_url",
    "video url",
    "image url",
    "invalid base64 image",
    "invalid base64 video",
    "invalid base64 media",
    "invalid base64 url",
    "invalid base64 data",
    "base64 image",
    "base64 video",
    "base64 media",
    "base64 url",
    "base64 data",
    "media type",
    "unsupported media",
    "unsupported image",
    "unsupported video",
    "unsupported format",
    "invalid image",
    "invalid video",
    "invalid media",
    "image format",
    "video format",
    "media format",
    "image decode",
    "video decode",
    "media parse",
    "media data",
    "image input",
    "video input",
    "image file",
    "video file",
    "failed to parse image",
    "failed to parse video",
    "failed to parse media",
    "image not valid",
    "video not valid",
    "media not valid",
)

# Chinese keywords found in API error messages. Chinese has no word-boundary
# concept, so these are matched as plain substrings.
MEDIA_MARKERS_ZH: tuple[str, ...] = (
    "媒体类型",
    "不支持的媒体",
    "不支持的图片",
    "不支持的视频",
    "不支持的格式",
    "图片格式",
    "视频格式",
    "媒体格式",
    "图片解码",
    "视频解码",
    "无效的图片",
    "无效的视频",
)

# All markers merged in their original order (English first, then Chinese).
# Kept as a single constant for external references and tests.
MEDIA_MARKERS: tuple[str, ...] = MEDIA_MARKERS_EN + MEDIA_MARKERS_ZH


def language() -> str:
    return os.environ.get("READ_IMAGE_LANGUAGE", "zh").strip().lower() or "zh"


def tr(zh: str, en: str) -> str:
    return en if language() == "en" else zh


class PluginError(RuntimeError):
    """Base class for all omnimodal plugin errors."""

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


class ReadImageError(PluginError):
    """Raised when image/video processing or the vision API fails."""


class VisionParameterError(ReadImageError):
    """Raised when the vision API rejects request parameters."""


class VisionApiError(ReadImageError):
    """Raised when the vision API returns an unexpected failure."""


class VisionNetworkError(ReadImageError):
    """Raised when the vision API cannot be reached."""


class VisionTimeoutError(VisionApiError):
    """Raised when the vision API does not respond within the profile timeout."""


class VisionRateLimitError(VisionApiError):
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


class CapturePageError(PluginError):
    """Raised when browser capture or interaction fails."""


class WindowsCaptureError(PluginError):
    """Raised when Windows screenshot or window enumeration fails."""


def normalize_error_code(error_code: str | None) -> str:
    return (error_code or "").lower().replace("_", "").replace("-", "")


def _matches_media_markers(text: str) -> bool:
    """Return True if *text* contains any media keyword.

    English markers are matched at word boundaries (``\\b``) to avoid false
    positives such as "video file" matching "video files"; Chinese markers
    have no word-boundary concept and fall back to substring matching.
    """
    return any(
        re.search(rf"\b{re.escape(marker)}\b", text) for marker in MEDIA_MARKERS_EN
    ) or any(marker in text for marker in MEDIA_MARKERS_ZH)


def is_media_error(
    status_code: int | None,
    error_code: str | None,
    detail: str,
) -> bool:
    """Classify API errors as media errors using structured fields first."""
    code = normalize_error_code(error_code)
    if code in MEDIA_ERROR_CODES:
        return True
    if any(fragment in code for fragment in MEDIA_CODE_FRAGMENTS):
        return True
    if status_code == 415:
        return True
    # Text-based fallback runs only when the status code is absent or is one
    # of 400/404/422: these commonly carry a JSON error body whose message is
    # the only signal. Other status codes (429/500, ...) usually come with an
    # explicit error code, so text matching is unnecessary and riskier.
    if status_code is None or status_code in {400, 404, 422}:
        text = f"{error_code or ''} {detail}".lower()
        return _matches_media_markers(text)
    return False
