from __future__ import annotations

import base64
import json
import os
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from read_image.concurrency import ConcurrencyGate
from read_image.config import MAX_RATE_LIMIT_RETRIES, MAX_TIMEOUT_RETRIES, api_key, env_int
from read_image.errors import (
    ReadImageError,
    VisionNetworkError,
    VisionRateLimitError,
    VisionTimeoutError,
    tr,
)
from read_image.http import _extract_error_metadata, _raise_api_error, _retry_delay, http_client
from read_image.profiles import profile_for_mode, video_timeout_for_mode


class VisionProvider(ABC):
    """Common vision API request and retry behavior."""

    provider_name = "base"
    supports_video_files = False

    def __init__(
        self,
        base_url: str,
        model: str,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client or http_client

    @property
    def cache_key(self) -> str:
        return f"{self.provider_name}:{self.model}"

    @abstractmethod
    def build_payload(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a provider-specific chat completion payload."""

    def _timeout_sec(
        self,
        mode: str | None,
        kind: str,
        explicit_timeout: int | None = None,
    ) -> int:
        if explicit_timeout is not None:
            return max(1, int(explicit_timeout))
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

    def _content_item(
        self,
        kind: str,
        content_url: str,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        if kind == "video":
            if file_id is not None:
                video_url: dict[str, Any] = {"file_id": file_id}
            else:
                video_url = {"url": content_url}
            return {
                "type": "video_url",
                "video_url": video_url,
            }
        return {
            "type": "image_url",
            "image_url": {"url": content_url},
        }

    def _post_chat(
        self,
        payload: dict[str, Any],
        timeout_sec: int,
        kind: str,
    ) -> str:
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key()}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout_sec,
            )
        except httpx.TimeoutException as exc:
            raise VisionTimeoutError(
                tr(
                    f"视觉接口调用超时（{timeout_sec}秒）",
                    f"Vision API call timed out ({timeout_sec}s)",
                )
            ) from exc
        except httpx.RequestError as exc:
            raise VisionNetworkError(
                tr(
                    "视觉接口网络调用失败。",
                    "Vision API network call failed.",
                )
            ) from exc

        if response.status_code >= 400:
            detail, error_code = _extract_error_metadata(response.text)
            _raise_api_error(
                kind,
                response.status_code,
                detail,
                error_code,
                retry_after=response.headers.get("Retry-After"),
            )

        try:
            response_payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ReadImageError(
                tr(
                    "视觉接口返回了非 JSON 响应。",
                    "Vision API returned non-JSON response.",
                )
            ) from exc

        choices = response_payload.get("choices") if isinstance(response_payload, dict) else None
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
        timeout_sec: int | None = None,
        gate: ConcurrencyGate | None = None,
        file_id: str | None = None,
    ) -> str:
        if file_id is not None and not self.supports_video_files:
            raise ReadImageError(
                tr(
                    "当前模型不支持视频文件上传。",
                    "The current model does not support video file uploads.",
                )
            )
        rate_attempts = 0
        timeout_attempts = 0
        while True:
            try:
                if gate:
                    gate.acquire()
                try:
                    result = self._post_chat(
                        self.build_payload(
                            kind,
                            content_url,
                            task,
                            mode,
                            file_id=file_id,
                        ),
                        self._timeout_sec(mode, kind, timeout_sec),
                        kind,
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
        timeout_sec: int | None = None,
        gate: ConcurrencyGate | None = None,
    ) -> str:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        return self.call_with_retries(
            "image",
            data_url,
            task,
            mode,
            timeout_sec=timeout_sec,
            gate=gate,
        )

    def call_video(
        self,
        video_url: str,
        task: str,
        mode: str | None,
        timeout_sec: int | None = None,
        file_id: str | None = None,
        gate: ConcurrencyGate | None = None,
    ) -> str:
        return self.call_with_retries(
            "video",
            video_url,
            task,
            mode,
            timeout_sec=timeout_sec,
            gate=gate,
            file_id=file_id,
        )

    def upload_video_file(self, path: str, timeout_sec: int | None = None) -> str:
        raise ReadImageError(
            tr(
                "当前模型不支持视频文件上传。",
                "The current model does not support video file uploads.",
            )
        )

    def delete_video_file(
        self,
        file_id: str,
        timeout_sec: int = 30,
        retries: int = 2,
    ) -> bool:
        return True
