from __future__ import annotations

import time
from typing import Any

import httpx

from read_image.concurrency import ConcurrencyGate
from read_image.config import api_key
from read_image.http import (
    _extract_error_metadata,
    _redact_sensitive_text,
    http_client,
    logger,
)
from read_image.providers.base import VisionProvider
from read_image.providers.factory import create_provider

MAX_RATE_LIMIT_RETRIES = 4
MAX_TIMEOUT_RETRIES = 1


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
        gate: ConcurrencyGate | None = None,
        video_file_id: str | None = None,
    ) -> str:
        return self._provider.call_with_retries(
            kind,
            content_url,
            task,
            mode,
            gate=gate,
            file_id=video_file_id,
        )

    def call_image(
        self,
        image_bytes: bytes,
        task: str,
        mode: str | None,
        mime_type: str = "image/jpeg",
        gate: ConcurrencyGate | None = None,
        timeout_sec: int | None = None,
    ) -> str:
        return self._provider.call_image(
            image_bytes,
            task,
            mode,
            mime_type=mime_type,
            gate=gate,
            timeout_sec=timeout_sec,
        )

    def call_video(
        self,
        video_url: str,
        task: str,
        mode: str | None,
        gate: ConcurrencyGate | None = None,
        timeout_sec: int | None = None,
    ) -> str:
        return self._provider.call_video(
            video_url,
            task,
            mode,
            gate=gate,
            timeout_sec=timeout_sec,
        )

    def call_video_file_id(
        self,
        file_id: str,
        task: str,
        mode: str | None,
        gate: ConcurrencyGate | None = None,
        timeout_sec: int | None = None,
    ) -> str:
        return self._provider.call_video(
            "",
            task,
            mode,
            gate=gate,
            timeout_sec=timeout_sec,
            file_id=file_id,
        )

    def upload_video_file(self, path: str, timeout_sec: int | None = None) -> str:
        return self._provider.upload_video_file(path, timeout_sec=timeout_sec)

    def delete_video_file(self, file_id: str) -> bool:
        return self._provider.delete_video_file(file_id)


default_client = VisionClient()


def _module_provider() -> VisionProvider:
    provider = create_provider()
    provider._client = http_client
    return provider


def call_image(
    image_bytes: bytes,
    task: str,
    mode: str | None,
    mime_type: str = "image/jpeg",
    gate: ConcurrencyGate | None = None,
    timeout_sec: int | None = None,
) -> str:
    return default_client.call_image(
        image_bytes,
        task,
        mode,
        mime_type=mime_type,
        gate=gate,
        timeout_sec=timeout_sec,
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


def upload_video_file(path: str, timeout_sec: int | None = None) -> str:
    return _module_provider().upload_video_file(path, timeout_sec=timeout_sec)


def delete_video_file(
    file_id: str,
    timeout_sec: int = 30,
    retries: int = 2,
) -> bool:
    return _module_provider().delete_video_file(
        file_id,
        timeout_sec=timeout_sec,
        retries=retries,
    )


__all__ = [
    "ConcurrencyGate",
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
