from __future__ import annotations

import atexit
import json
import logging
import re
from typing import Any

import httpx

from omnimodal.config import api_key
from omnimodal.errors import (
    VisionApiError,
    VisionMediaError,
    VisionParameterError,
    VisionRateLimitError,
    is_media_error,
    tr,
)
from omnimodal.logging import configure_logging
from omnimodal.urls import validate_remote_url

logger = configure_logging("read-image-http")
_DATA_URL_TOKEN = re.compile(r"(data:[^,]+;base64,)[A-Za-z0-9+/=]+")
_LONG_BASE64_TOKEN = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")
_QUERY_PARAM_TOKEN = re.compile(r"([?&][^=&\s]+=)[^&\s]+")
HTTP_MAX_CONNECTIONS = 20
HTTP_MAX_KEEPALIVE_CONNECTIONS = 10
RATE_LIMIT_BACKOFF_BASE = 2.0
RATE_LIMIT_BACKOFF_MAX = 16.0


def _safe_request_log(request: httpx.Request) -> None:
    logger.info(
        "http request method=%s url=%s",
        request.method,
        _redact_sensitive_text(str(request.url)),
    )


def _safe_response_log(response: httpx.Response) -> None:
    logger.info(
        "http response method=%s url=%s status=%s",
        response.request.method,
        _redact_sensitive_text(str(response.request.url)),
        response.status_code,
    )


class SafeHTTPTransport(httpx.HTTPTransport):
    """Validate destination before every connection, including redirects."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        validate_remote_url(str(request.url))
        return super().handle_request(request)


http_client = httpx.Client(
    transport=SafeHTTPTransport(),
    timeout=httpx.Timeout(120.0, connect=10.0),
    follow_redirects=True,
    limits=httpx.Limits(
        max_connections=HTTP_MAX_CONNECTIONS,
        max_keepalive_connections=HTTP_MAX_KEEPALIVE_CONNECTIONS,
    ),
    event_hooks={
        "request": [_safe_request_log],
        "response": [_safe_response_log],
    },
)
atexit.register(http_client.close)
logging.getLogger("httpx").setLevel(logging.WARNING)


def _safe_text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _redact_sensitive_text(value: str) -> str:
    value = _DATA_URL_TOKEN.sub(r"\1[REDACTED]", value)
    value = _LONG_BASE64_TOKEN.sub("[REDACTED]", value)
    value = _QUERY_PARAM_TOKEN.sub(r"\1[REDACTED]", value)
    try:
        key = api_key()
    except Exception:
        return value
    if key:
        value = value.replace(key, "[REDACTED]")
    return value


def _extract_error_metadata(body: str) -> tuple[str, str | None]:
    body = body.strip()
    if not body:
        return tr("(空响应体)", "(empty response body)"), None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return tr("(非 JSON 错误响应)", "(non-JSON error response)"), None

    if not isinstance(parsed, dict):
        return tr("(未知错误响应)", "(unknown error response)"), None

    error = parsed.get("error")
    code: Any = None
    message: Any = None
    if isinstance(error, dict):
        code = error.get("code") or error.get("type") or error.get("error_code")
        message = error.get("message") or error.get("detail")
    elif isinstance(error, str):
        message = error
    else:
        code = parsed.get("code") or parsed.get("type") or parsed.get("error_code")
        message = parsed.get("message") or parsed.get("detail") or parsed.get("error")

    raw_detail = _safe_text(message, limit=2000) or tr(
        "(无错误详情)",
        "(no error detail)",
    )
    detail = _redact_sensitive_text(raw_detail)[:500]
    raw_error_code = _safe_text(code, limit=200)
    error_code = _redact_sensitive_text(raw_error_code) if raw_error_code else None
    return detail, error_code


def _extract_error_detail(body: str) -> str:
    detail, _ = _extract_error_metadata(body)
    return detail


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            value = float(retry_after)
            if value >= 0:
                return min(60.0, value)
        except ValueError:
            pass
    return min(
        RATE_LIMIT_BACKOFF_MAX,
        RATE_LIMIT_BACKOFF_BASE**attempt,
    )


def _raise_api_error(
    kind: str,
    status_code: int,
    detail: str,
    error_code: str | None,
    retry_after: str | None = None,
) -> None:
    logger.warning(
        "vision api error kind=%s status=%s error_code=%s",
        kind,
        status_code,
        error_code or "-",
    )
    if status_code == 429:
        raise VisionRateLimitError(
            tr(
                "视觉接口限流。",
                "Vision API rate limited.",
            ),
            retry_after=retry_after,
            error_code=error_code,
            detail=detail,
        )
    if is_media_error(status_code, error_code, detail):
        raise VisionMediaError(
            tr(
                "视觉接口媒体参数错误。",
                "Vision API media error.",
            ),
            status_code=status_code,
            error_code=error_code,
            detail=detail,
        )
    error_type = VisionParameterError if status_code in {400, 422} else VisionApiError
    raise error_type(
        tr(
            f"视觉接口调用失败（HTTP {status_code}）。",
            f"Vision API call failed (HTTP {status_code}).",
        ),
        status_code=status_code,
        error_code=error_code,
        detail=detail,
    )
