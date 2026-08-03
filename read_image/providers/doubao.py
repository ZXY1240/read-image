from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx

from read_image.config import (
    MAX_RATE_LIMIT_RETRIES,
    api_key,
    video_files_api_timeout_sec,
)
from read_image.errors import (
    ReadImageError,
    VisionNetworkError,
    VisionRateLimitError,
    tr,
)
from read_image.http import (
    _extract_error_metadata,
    _raise_api_error,
    _retry_delay,
    logger,
)
from read_image.profiles import profile_for_mode, video_prompt_for_mode
from read_image.providers.base import VisionProvider

FILE_POLL_INTERVAL_SEC = 2.0


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


class DoubaoProvider(VisionProvider):
    """Doubao/Ark provider with Files API support for local video uploads."""

    provider_name = "doubao"
    supports_video_files = True

    def build_payload(
        self,
        kind: str,
        content_url: str,
        task: str,
        mode: str | None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        profile = profile_for_mode(mode)
        if kind == "video":
            system_prompt = video_prompt_for_mode(profile.key)
        else:
            system_prompt = profile.system_prompt
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": task},
                        self._content_item(kind, content_url, file_id=file_id),
                    ],
                },
            ],
            "thinking": {"type": "enabled" if profile.thinking_enabled else "disabled"},
        }
        if profile.max_tokens is not None:
            payload["max_tokens"] = profile.max_tokens
        return payload

    def _post_video_file(self, path: Path, timeout: float) -> dict[str, Any]:
        mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
        try:
            with path.open("rb") as file_handle:
                response = self._client.post(
                    f"{self.base_url}/files",
                    headers={"Authorization": f"Bearer {api_key()}"},
                    data={"purpose": "user_data"},
                    files={"file": (path.name, file_handle, mime)},
                    timeout=timeout,
                )
        except httpx.TimeoutException as exc:
            raise ReadImageError(tr("视频文件上传超时。", "Video file upload timed out.")) from exc
        except httpx.RequestError as exc:
            raise VisionNetworkError(tr("视频文件上传失败。", "Video file upload failed.")) from exc

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

    def _upload_video_file(self, path: Path, deadline: float) -> tuple[str, str]:
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
                payload = self._post_video_file(path, max(1.0, remaining))
                file_id = _file_id_from_payload(payload)
                if time.monotonic() >= deadline:
                    self.delete_video_file(file_id)
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

    def _file_status(self, file_id: str, timeout: float) -> str:
        try:
            response = self._client.get(
                f"{self.base_url}/files/{file_id}",
                headers={"Authorization": f"Bearer {api_key()}"},
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise ReadImageError(
                tr("视频文件状态查询超时。", "Video file status check timed out.")
            ) from exc
        except httpx.RequestError as exc:
            raise VisionNetworkError(
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

    def _wait_for_file_processed(self, file_id: str, deadline: float) -> str:
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
                status = self._file_status(file_id, max(1.0, remaining))
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
                raise ReadImageError(tr("视频文件处理失败。", "Video file processing failed."))
            time.sleep(min(FILE_POLL_INTERVAL_SEC, max(0.1, remaining)))

    def upload_video_file(
        self,
        path: Path | str,
        timeout_sec: int | None = None,
    ) -> str:
        video_path = Path(path)
        timeout = max(1, timeout_sec or video_files_api_timeout_sec())
        deadline = time.monotonic() + timeout
        file_id, status = self._upload_video_file(video_path, deadline)
        if status in {"processed", "ready"}:
            return file_id
        try:
            return self._wait_for_file_processed(file_id, deadline)
        except ReadImageError:
            self.delete_video_file(file_id)
            raise

    def delete_video_file(
        self,
        file_id: str,
        timeout_sec: int = 30,
        retries: int = 2,
    ) -> bool:
        for attempt in range(retries + 1):
            try:
                response = self._client.delete(
                    f"{self.base_url}/files/{file_id}",
                    headers={"Authorization": f"Bearer {api_key()}"},
                    timeout=max(1, timeout_sec),
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                logger.warning(
                    "video file cleanup failed attempt=%s kind=%s",
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt >= retries:
                    return False
                continue
            if response.status_code >= 400:
                _, error_code = _extract_error_metadata(response.text)
                logger.warning(
                    "video file cleanup failed attempt=%s status=%s error_code=%s",
                    attempt + 1,
                    response.status_code,
                    error_code or "-",
                )
                if attempt >= retries:
                    return False
                continue
            return True
        return False
