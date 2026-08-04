from __future__ import annotations

import time
from typing import Any

import httpx

from omnimodal.config import (
    MAX_RATE_LIMIT_RETRIES,
    MAX_TIMEOUT_RETRIES,
    api_key,
)
from omnimodal.http import (
    _extract_error_metadata,
    _redact_sensitive_text,
    http_client,
    logger,
)
from omnimodal.providers.base import VisionProvider
from omnimodal.providers.factory import create_provider


class VisionClient:
    """Compatibility facade over the active VisionProvider."""

    def __init__(
        self,
        provider: VisionProvider | None = None,
        client: httpx.Client | None = None,
    ):
        self._provider = provider or create_provider()
        if client is not None:
            self._provider._client = client

    @property
    def base_url(self) -> str:
        return self._provider.base_url

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def provider(self) -> VisionProvider:
        return self._provider

    def build_payload(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        video_file_id: str | None = None,
    ) -> dict[str, Any]:
        return self._provider.build_payload(
            kind,
            content_url,
            task,
            mode,
            file_id=video_file_id,
        )

    def _call(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        video_file_id: str | None = None,
    ) -> str:
        return self._provider.call_with_retries(
            kind,
            content_url,
            task,
            mode,
            file_id=video_file_id,
        )

    def call_with_retries(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        video_file_id: str | None = None,
    ) -> str:
        return self._provider.call_with_retries(
            kind,
            content_url,
            task,
            mode,
            file_id=video_file_id,
        )

    def call_image(
        self,
        image_bytes: bytes,
        task: str,
        mode: str | None,
        mime_type: str = "image/jpeg",
        timeout_sec: int | None = None,
    ) -> str:
        return self._provider.call_image(
            image_bytes,
            task,
            mode,
            mime_type=mime_type,
            timeout_sec=timeout_sec,
        )

    def call_video(
        self,
        video_url: str,
        task: str,
        mode: str | None,
        timeout_sec: int | None = None,
    ) -> str:
        return self._provider.call_video(
            video_url,
            task,
            mode,
            timeout_sec=timeout_sec,
        )

    def call_video_file_id(
        self,
        file_id: str,
        task: str,
        mode: str | None,
        timeout_sec: int | None = None,
    ) -> str:
        return self._provider.call_video(
            "",
            task,
            mode,
            timeout_sec=timeout_sec,
            file_id=file_id,
        )

    def upload_video_file(self, path: str, timeout_sec: int | None = None) -> str:
        return self._provider.upload_video_file(path, timeout_sec=timeout_sec)

    def delete_video_file(
        self,
        file_id: str,
        timeout_sec: int = 30,
        retries: int = 2,
    ) -> bool:
        return self._provider.delete_video_file(
            file_id,
            timeout_sec=timeout_sec,
            retries=retries,
        )


default_client = VisionClient()


def call_image(
    image_bytes: bytes,
    task: str,
    mode: str | None,
    mime_type: str = "image/jpeg",
    timeout_sec: int | None = None,
) -> str:
    return default_client.call_image(
        image_bytes,
        task,
        mode,
        mime_type=mime_type,
        timeout_sec=timeout_sec,
    )


def call_video(
    video_url: str,
    task: str,
    mode: str | None,
) -> str:
    return default_client.call_video(video_url, task, mode)


def call_video_file_id(
    file_id: str,
    task: str,
    mode: str | None,
) -> str:
    return default_client.call_video_file_id(file_id, task, mode)


def _sync_module_http_client() -> None:
    """让 default_client 的 provider 跟随当前模块级 http_client。

    default_client 在模块导入时创建,其 provider 的 client 是当时的绑定
    快照;模块级入口始终面向全局共享的 http_client,调用前同步一次,
    保证模块级入口与 default_client 共享同一 provider 和同一 client。
    """
    default_client.provider._client = http_client


def upload_video_file(path: str, timeout_sec: int | None = None) -> str:
    _sync_module_http_client()
    return default_client.upload_video_file(path, timeout_sec=timeout_sec)


def delete_video_file(
    file_id: str,
    timeout_sec: int = 30,
    retries: int = 2,
) -> bool:
    _sync_module_http_client()
    return default_client.delete_video_file(
        file_id,
        timeout_sec=timeout_sec,
        retries=retries,
    )


__all__ = [
    "MAX_RATE_LIMIT_RETRIES",
    "MAX_TIMEOUT_RETRIES",
    "VisionClient",
    "call_image",
    "call_video",
    "call_video_file_id",
    "default_client",
    "delete_video_file",
    "http_client",
    "upload_video_file",
    "api_key",
    "_extract_error_metadata",
    "_redact_sensitive_text",
    "time",
    "logger",
]
