from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from read_image.config import (
    MAX_RATE_LIMIT_RETRIES,
    MAX_TIMEOUT_RETRIES,
    api_key,
    base_url,
    env_int,
    model_name,
    video_files_api_timeout_sec,
)
from read_image.errors import (
    ReadImageError,
    VisionMediaError,
    VisionRateLimitError,
    VisionTimeoutError,
    tr,
)
from read_image.logging import configure_logging
from read_image.profiles import (
    profile_for_mode,
    video_prompt_for_mode,
    video_timeout_for_mode,
)

logger = configure_logging("read-image-api")
http_client = httpx.Client(
    timeout=httpx.Timeout(120.0, connect=10.0),
    follow_redirects=True,
)
_DATA_URL_TOKEN = re.compile(r"(data:[^,]+;base64,)[A-Za-z0-9+/=]+")
_LONG_BASE64_TOKEN = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")


def _safe_text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _redact_sensitive_text(value: str) -> str:
    value = _DATA_URL_TOKEN.sub(r"\1[REDACTED]", value)
    value = _LONG_BASE64_TOKEN.sub("[REDACTED]", value)
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
    error_code = (
        _redact_sensitive_text(raw_error_code) if raw_error_code else None
    )
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
    return min(16.0, 2.0**attempt)


def _normalized_error_code(error_code: str | None) -> str:
    return (error_code or "").lower().replace("_", "").replace("-", "")


def _looks_like_media_error(
    status_code: int | None,
    error_code: str | None,
    detail: str,
) -> bool:
    code = _normalized_error_code(error_code)
    media_codes = {
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
    if code in media_codes:
        return True
    if any(
        marker in code
        for marker in (
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
    ):
        return True
    if status_code == 415:
        return True
    if status_code in {400, 404, 422}:
        text = f"{error_code or ''} {detail}".lower()
        media_markers = (
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
        return any(marker in text for marker in media_markers)
    return False


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
    if _looks_like_media_error(status_code, error_code, detail):
        raise VisionMediaError(
            tr(
                "视觉接口媒体参数错误。",
                "Vision API media error.",
            ),
            status_code=status_code,
            error_code=error_code,
            detail=detail,
        )
    raise ReadImageError(
        tr(
            f"视觉接口调用失败（HTTP {status_code}）。",
            f"Vision API call failed (HTTP {status_code}).",
        ),
        status_code=status_code,
        error_code=error_code,
        detail=detail,
    )


def _file_id_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ReadImageError(
            tr(
                "视频文件上传接口返回异常。",
                "Video file upload API returned invalid data.",
            )
        )
    file_id = payload.get("id") or payload.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise ReadImageError(
            tr(
                "视频文件上传接口未返回文件 ID。",
                "Video file upload API did not return a file ID.",
            )
        )
    return file_id


def _post_video_file(path: Path, timeout: float) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    try:
        with path.open("rb") as file_handle:
            response = http_client.post(
                f"{base_url()}/files",
                headers={"Authorization": f"Bearer {api_key()}"},
                data={"purpose": "user_data"},
                files={
                    "file": (path.name, file_handle, mime)
                },
                timeout=timeout,
            )
    except httpx.TimeoutException as exc:
        raise ReadImageError(
            tr("视频文件上传超时。", "Video file upload timed out.")
        ) from exc
    except httpx.RequestError as exc:
        raise ReadImageError(
            tr("视频文件上传失败。", "Video file upload failed.")
        ) from exc

    if response.status_code >= 400:
        detail, error_code = _extract_error_metadata(response.text)
        _raise_api_error(
            "video-file",
            response.status_code,
            detail,
            error_code,
            retry_after=response.headers.get("Retry-After"),
        )

    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise ReadImageError(
            tr(
                "视频文件上传接口返回了非 JSON 响应。",
                "Video file upload API returned non-JSON response.",
            )
        ) from exc
    if not isinstance(payload, dict):
        raise ReadImageError(
            tr(
                "视频文件上传接口返回异常。",
                "Video file upload API returned invalid data.",
            )
        )
    return payload


def _upload_video_file(path: Path, deadline: float) -> tuple[str, str]:
    rate_attempts = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadImageError(
                tr(
                    "视频文件处理超时。",
                    "Video file processing timed out.",
                )
            )
        try:
            payload = _post_video_file(path, max(1.0, remaining))
            file_id = _file_id_from_payload(payload)
            if time.monotonic() >= deadline:
                delete_video_file(file_id)
                raise ReadImageError(
                    tr(
                        "视频文件处理超时。",
                        "Video file processing timed out.",
                    )
                )
            return file_id, str(payload.get("status") or "").lower()
        except VisionRateLimitError as exc:
            if rate_attempts >= MAX_RATE_LIMIT_RETRIES:
                raise
            delay = _retry_delay(rate_attempts, exc.retry_after)
            if delay >= remaining:
                raise ReadImageError(
                    tr(
                        "视频文件处理超时。",
                        "Video file processing timed out.",
                    )
                ) from exc
            time.sleep(delay)
            rate_attempts += 1


def _file_status(file_id: str, timeout: float) -> str:
    try:
        response = http_client.get(
            f"{base_url()}/files/{file_id}",
            headers={"Authorization": f"Bearer {api_key()}"},
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise ReadImageError(
            tr("视频文件状态查询超时。", "Video file status check timed out.")
        ) from exc
    except httpx.RequestError as exc:
        raise ReadImageError(
            tr("视频文件状态查询失败。", "Video file status check failed.")
        ) from exc

    if response.status_code >= 400:
        detail, error_code = _extract_error_metadata(response.text)
        _raise_api_error(
            "video-file",
            response.status_code,
            detail,
            error_code,
            retry_after=response.headers.get("Retry-After"),
        )

    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise ReadImageError(
            tr(
                "视频文件状态接口返回了非 JSON 响应。",
                "Video file status API returned non-JSON response.",
            )
        ) from exc
    if not isinstance(payload, dict):
        raise ReadImageError(
            tr(
                "视频文件状态接口返回异常。",
                "Video file status API returned invalid data.",
            )
        )
    status = payload.get("status")
    if status is None:
        raise ReadImageError(
            tr(
                "视频文件状态接口未返回状态。",
                "Video file status API did not return a status.",
            )
        )
    return str(status).lower()


def _wait_for_file_processed(file_id: str, deadline: float) -> str:
    rate_attempts = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadImageError(
                tr(
                    "视频文件处理超时。",
                    "Video file processing timed out.",
                )
            )
        try:
            status = _file_status(file_id, max(1.0, remaining))
        except VisionRateLimitError as exc:
            if rate_attempts >= MAX_RATE_LIMIT_RETRIES:
                raise
            delay = _retry_delay(rate_attempts, exc.retry_after)
            if delay >= remaining:
                raise ReadImageError(
                    tr(
                        "视频文件处理超时。",
                        "Video file processing timed out.",
                    )
                ) from exc
            time.sleep(delay)
            rate_attempts += 1
            continue
        if status in {"processed", "ready"}:
            return file_id
        if status in {"failed", "error", "deleted", "cancelled"}:
            raise ReadImageError(
                tr("视频文件处理失败。", "Video file processing failed.")
            )
        time.sleep(min(2.0, max(0.1, remaining)))


def upload_video_file(path: Path, timeout_sec: int | None = None) -> str:
    timeout = max(1, timeout_sec or video_files_api_timeout_sec())
    deadline = time.monotonic() + timeout
    file_id, status = _upload_video_file(path, deadline)
    if status in {"processed", "ready"}:
        return file_id
    try:
        return _wait_for_file_processed(file_id, deadline)
    except ReadImageError:
        delete_video_file(file_id)
        raise


def delete_video_file(file_id: str, timeout_sec: int = 30) -> None:
    try:
        response = http_client.delete(
            f"{base_url()}/files/{file_id}",
            headers={"Authorization": f"Bearer {api_key()}"},
            timeout=max(1, timeout_sec),
        )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning(
            "video file cleanup failed kind=%s",
            type(exc).__name__,
        )
        return
    if response.status_code >= 400:
        detail, error_code = _extract_error_metadata(response.text)
        logger.warning(
            "video file cleanup failed status=%s detail=%s error_code=%s",
            response.status_code,
            detail,
            error_code or "-",
        )


class ConcurrencyGate:
    """Bounds active vision requests and recovers after successful calls."""

    def __init__(self, limit: int, recovery_threshold: int = 8):
        self._initial_limit = max(1, limit)
        self._limit = max(1, limit)
        self._recovery_threshold = max(1, recovery_threshold)
        self._condition = threading.Condition()
        self._active = 0
        self._rate_limit_hits = 0
        self._successes = 0

    def acquire(self) -> None:
        with self._condition:
            while self._active >= self._limit:
                self._condition.wait()
            self._active += 1

    def release(self) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    def note_rate_limit(self) -> None:
        with self._condition:
            self._rate_limit_hits += 1
            self._successes = 0
            if self._rate_limit_hits >= 2 and self._limit > 1:
                self._limit -= 1
                self._rate_limit_hits = 0
                self._condition.notify_all()

    def note_success(self) -> None:
        with self._condition:
            self._rate_limit_hits = 0
            self._successes += 1
            if (
                self._successes >= self._recovery_threshold
                and self._limit < self._initial_limit
            ):
                self._limit += 1
                self._successes = 0
                self._condition.notify_all()

    @property
    def current_limit(self) -> int:
        with self._condition:
            return self._limit


class VisionClient:
    """Shared client for image and video chat completions."""

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or http_client

    @property
    def base_url(self) -> str:
        return base_url()

    @property
    def model(self) -> str:
        return model_name()

    def _timeout_sec(self, mode: str | None, kind: str) -> int:
        profile = profile_for_mode(mode)
        if kind == "video":
            timeout = video_timeout_for_mode(profile.key)
            if os.environ.get("READ_VIDEO_TIMEOUT_SEC", "").strip():
                timeout = max(1, env_int("READ_VIDEO_TIMEOUT_SEC", timeout))
        else:
            timeout = profile.timeout_sec
            if os.environ.get("READ_IMAGE_TIMEOUT_SEC", "").strip():
                timeout = max(1, env_int("READ_IMAGE_TIMEOUT_SEC", timeout))
        return timeout

    def build_payload(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        video_file_id: str | None = None,
    ) -> dict[str, Any]:
        profile = profile_for_mode(mode)
        if kind == "video":
            system_prompt = video_prompt_for_mode(profile.key)
            if video_file_id:
                video_url: dict[str, Any] = {"file_id": video_file_id}
            else:
                video_url = {"url": content_url}
            content_item: dict[str, Any] = {
                "type": "video_url",
                "video_url": video_url,
            }
        else:
            system_prompt = profile.system_prompt
            content_item = {
                "type": "image_url",
                "image_url": {"url": content_url},
            }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": task},
                        content_item,
                    ],
                },
            ],
            "thinking": {
                "type": "enabled" if profile.thinking_enabled else "disabled"
            },
        }
        if profile.max_tokens is not None:
            payload["max_tokens"] = profile.max_tokens
        return payload

    def _raise_api_error(
        self,
        kind: str,
        status_code: int,
        detail: str,
        error_code: str | None,
        retry_after: str | None = None,
    ) -> None:
        _raise_api_error(
            kind,
            status_code,
            detail,
            error_code,
            retry_after=retry_after,
        )

    def _call(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        video_file_id: str | None = None,
    ) -> str:
        timeout = self._timeout_sec(mode, kind)
        payload = self.build_payload(
            kind,
            content_url,
            task,
            mode,
            video_file_id=video_file_id,
        )
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key()}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise VisionTimeoutError(
                tr(
                    f"视觉接口调用超时（{timeout}秒）",
                    f"Vision API call timed out ({timeout}s)",
                )
            ) from exc
        except httpx.RequestError as exc:
            raise ReadImageError(
                tr(
                    "视觉接口网络调用失败。",
                    "Vision API network call failed.",
                )
            ) from exc

        if response.status_code >= 400:
            detail, error_code = _extract_error_metadata(response.text)
            self._raise_api_error(
                kind,
                response.status_code,
                detail,
                error_code,
                retry_after=response.headers.get("Retry-After"),
            )

        body = response.text
        try:
            response_payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ReadImageError(
                tr(
                    "视觉接口返回了非 JSON 响应。",
                    "Vision API returned non-JSON response.",
                )
            ) from exc

        choices = (
            response_payload.get("choices")
            if isinstance(response_payload, dict)
            else None
        )
        if not isinstance(choices, list) or not choices:
            raise ReadImageError(
                tr(
                    "视觉接口返回异常：响应中没有 choices。",
                    "Vision API response missing choices.",
                )
            )
        if not isinstance(choices[0], dict):
            raise ReadImageError(
                tr(
                    "视觉接口返回异常：choices[0] 不是对象。",
                    "Vision API response choices[0] is not an object.",
                )
            )

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ReadImageError(
                tr(
                    "视觉接口返回异常：choices[0].message 缺失。",
                    "Vision API response missing choices[0].message.",
                )
            )

        content = message.get("content")
        if content is None:
            raise ReadImageError(
                tr(
                    "视觉接口返回异常：choices[0].message.content 为空。",
                    "Vision API response content is empty.",
                )
            )
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False)

    def call_with_retries(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        gate: ConcurrencyGate | None = None,
        video_file_id: str | None = None,
    ) -> str:
        rate_attempts = 0
        timeout_attempts = 0
        while True:
            try:
                if gate:
                    gate.acquire()
                try:
                    result = self._call(
                        kind,
                        content_url,
                        task,
                        mode,
                        video_file_id=video_file_id,
                    )
                finally:
                    if gate:
                        gate.release()
                if gate:
                    gate.note_success()
                return result
            except VisionRateLimitError as exc:
                if gate:
                    gate.note_rate_limit()
                if rate_attempts >= MAX_RATE_LIMIT_RETRIES:
                    raise
                time.sleep(_retry_delay(rate_attempts, exc.retry_after))
                rate_attempts += 1
            except VisionTimeoutError:
                if timeout_attempts >= MAX_TIMEOUT_RETRIES:
                    raise
                time.sleep(1.0)
                timeout_attempts += 1

    def call_image(
        self,
        image_bytes: bytes,
        task: str,
        mode: str | None,
        mime_type: str = "image/jpeg",
        gate: ConcurrencyGate | None = None,
    ) -> str:
        data_url = (
            f"data:{mime_type};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        return self.call_with_retries("image", data_url, task, mode, gate=gate)

    def call_video(
        self,
        video_url: str,
        task: str,
        mode: str | None,
        gate: ConcurrencyGate | None = None,
    ) -> str:
        return self.call_with_retries("video", video_url, task, mode, gate=gate)

    def call_video_file_id(
        self,
        file_id: str,
        task: str,
        mode: str | None,
        gate: ConcurrencyGate | None = None,
    ) -> str:
        return self.call_with_retries(
            "video",
            "",
            task,
            mode,
            gate=gate,
            video_file_id=file_id,
        )


default_client = VisionClient()


def call_image(
    image_bytes: bytes,
    task: str,
    mode: str | None,
    mime_type: str = "image/jpeg",
    gate: ConcurrencyGate | None = None,
) -> str:
    return default_client.call_image(
        image_bytes,
        task,
        mode,
        mime_type=mime_type,
        gate=gate,
    )


def call_video(
    video_url: str,
    task: str,
    mode: str | None,
    gate: ConcurrencyGate | None = None,
) -> str:
    return default_client.call_video(video_url, task, mode, gate=gate)


def call_video_file_id(
    file_id: str,
    task: str,
    mode: str | None,
    gate: ConcurrencyGate | None = None,
) -> str:
    return default_client.call_video_file_id(file_id, task, mode, gate=gate)
