"""Async generation tasks for DashScope (Wanx text-to-image/video, TTS, ASR).

DashScope generation APIs are asynchronous: submit a task, poll by task_id,
then download the result URL (valid 24h). This module provides a uniform
pipeline: submit -> poll -> fetch result -> download to local output dir.

The only state-changing actions here are HTTP requests; retries are limited
to idempotent operations (polling, downloading). A failed *submit* is never
silently retried because the server may have already created the task and a
second submit would double-bill.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnimodal.config import (
    api_key,
)
from omnimodal.config import (
    generation_timeout_sec as config_generation_timeout_sec,
)
from omnimodal.config import (
    max_video_duration as config_max_video_duration,
)
from omnimodal.errors import ReadImageError, tr
from omnimodal.http import http_client
from omnimodal.urls import validate_remote_url

DEFAULT_POLL_INTERVAL_SEC = 10
DEFAULT_GENERATION_TIMEOUT_SEC = 300
DEFAULT_VIDEO_GENERATION_TIMEOUT_SEC = 900
DEFAULT_MAX_VIDEO_DURATION = 15
DEFAULT_POLL_RETRIES = 2

# Result URLs are only valid 24h; download immediately after success.
RESULT_URL_TTL_SEC = 24 * 3600

# Per-model price hints (yuan). Source: user's qianwenai.com model marketplace
# screenshots (2026-08-05). Kept in sync with README pricing table.
PRICE_IMAGE_WAN27 = 0.50  # wan2.7-image-pro, per image
PRICE_IMAGE_QWEN20 = 0.20  # qwen-image-2.0, per image
PRICE_VIDEO_WAN27_PER_SEC = 0.60  # wan2.7-t2v, 0.6-1 yuan/sec (use low end)
PRICE_VIDEO_HAPPYHORSE_PER_SEC = 0.27  # happyhorse-1.1-t2v, 0.27-0.72 yuan/sec
PRICE_TTS_PER_10K_CHARS = 1.00  # qwen-audio-3.0-tts
PRICE_TTS_COSY_V35_PER_10K = 1.50  # cosyvoice-v3.5-plus


@dataclass
class GenerationSpec:
    """Static config for one generation task type."""

    endpoint: str
    model: str
    poll_interval: int = DEFAULT_POLL_INTERVAL_SEC
    timeout_sec: int = DEFAULT_GENERATION_TIMEOUT_SEC
    price_hint: float = 0.0  # yuan per unit, for user-facing cost note
    price_unit: str = ""

    @property
    def cost_label(self) -> str:
        if not self.price_hint:
            return tr("费用未知", "cost unknown")
        return f"≈ {self.price_hint:.2f} 元/{self.price_unit}"


def _auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


class GenerationClient:
    """Submit and poll a DashScope async task, then download the result."""

    def __init__(
        self,
        spec: GenerationSpec,
        output_dir: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        self.spec = spec
        self.output_dir = Path(output_dir) if output_dir else None
        self.extra_headers = dict(extra_headers or {})

    # ---- submit ----
    def submit(self, payload: dict[str, Any]) -> str:
        """Submit a task and return task_id. Never retries a failed submit."""
        try:
            response = http_client.post(
                self.spec.endpoint,
                headers=_auth_headers(
                    {
                        "X-DashScope-Async": "enable",
                        **self.extra_headers,
                    }
                ),
                json=payload,
                timeout=60.0,
            )
        except Exception as exc:
            raise ReadImageError(
                tr(
                    "提交生成任务失败（网络错误）。",
                    "Failed to submit generation task (network error).",
                )
            ) from exc
        if response.status_code >= 400:
            raise ReadImageError(
                tr(
                    f"提交生成任务失败（HTTP {response.status_code}）。",
                    f"Failed to submit generation task (HTTP {response.status_code}).",
                )
            )
        try:
            parsed = response.json()
        except json.JSONDecodeError as exc:
            raise ReadImageError(
                tr(
                    "提交生成任务返回了非 JSON 响应。",
                    "Generation submit response is not JSON.",
                )
            ) from exc
        task_id = parsed.get("output", {}).get("task_id") if isinstance(parsed, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise ReadImageError(
                tr(
                    "提交生成任务响应缺少 task_id。",
                    "Generation submit response is missing task_id.",
                )
            )
        return task_id

    # ---- poll ----
    def poll_status(self, task_id: str) -> dict[str, Any]:
        """Query task status. Retries transient failures (idempotent).

        4xx 是客户端错误（如 task_id 失效），重试无意义，直接抛错；
        仅 5xx / 网络错误 / 429 重试。
        """
        url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        last_error: Exception | None = None
        for _attempt in range(DEFAULT_POLL_RETRIES + 1):
            try:
                response = http_client.get(url, headers=_auth_headers(), timeout=30.0)
            except Exception as exc:
                last_error = exc
                time.sleep(1.0)
                continue
            if response.status_code < 400:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    last_error = ReadImageError("poll response not JSON")
                    continue
            if 400 <= response.status_code < 500:
                raise ReadImageError(
                    tr(
                        "查询任务状态失败（HTTP {code}）。",
                        "Failed to query task status (HTTP {code}).",
                    ).format(code=response.status_code)
                )
            last_error = ReadImageError(f"poll HTTP {response.status_code}: {response.text[:200]}")
            time.sleep(1.0)
        raise ReadImageError(
            tr(
                "查询任务状态失败，请稍后重试。",
                "Failed to query task status, please retry later.",
            )
        ) from last_error

    def wait_for_result(
        self,
        task_id: str,
        progress_cb: Callable[[int, int | None, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Poll until SUCCEEDED/FAILED or timeout. Returns the output dict.

        On timeout, raises GenerationTimeoutError carrying task_id so callers
        can tell the user to query later instead of blocking.
        """
        deadline = time.monotonic() + self.spec.timeout_sec
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            if progress_cb:
                progress_cb(
                    0,
                    None,
                    tr(
                        f"任务处理中（已等待 {int(attempts * self.spec.poll_interval)}s）…",
                        f"Task in progress (waited {int(attempts * self.spec.poll_interval)}s)…",
                    ),
                )
            data = self.poll_status(task_id)
            status = data.get("output", {}).get("task_status") if isinstance(data, dict) else None
            if status == "SUCCEEDED":
                return data
            if status in {"FAILED", "CANCELED"}:
                output = data.get("output", {}) if isinstance(data, dict) else {}
                detail = output.get("message") or output.get("error") or ""
                error_code = output.get("code") or output.get("error_code") or ""
                suffix = f" {error_code}: {detail}" if error_code or detail else ""
                raise ReadImageError(
                    tr(
                        f"生成任务{status}。",
                        f"Generation task {status}.",
                    )
                    + suffix
                )
            if status == "UNKNOWN":
                raise ReadImageError(
                    tr(
                        "任务不存在或已超过 24 小时有效期。",
                        "Task not found or older than 24 hours.",
                    )
                )
            time.sleep(self.spec.poll_interval)
        raise GenerationTimeoutError(task_id)

    # ---- download ----
    def download_result(self, result_url: str, filename: str) -> Path:
        """Download a result URL to the output dir (must exist & be allowed)."""
        if not self.output_dir:
            raise ReadImageError(
                tr(
                    "未配置生成结果输出目录。",
                    "No generation output directory configured.",
                )
            )
        validate_remote_url(result_url)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / filename
        try:
            with http_client.stream("GET", result_url, timeout=60.0) as response:
                response.raise_for_status()
                with target.open("wb") as fh:
                    for chunk in response.iter_bytes():
                        fh.write(chunk)
        except Exception as exc:
            raise ReadImageError(
                tr(
                    "下载生成结果失败。",
                    "Failed to download generation result.",
                )
            ) from exc
        return target


class GenerationTimeoutError(ReadImageError):
    """Task still running after the timeout. Holds task_id for later query."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(
            tr(
                f"生成任务仍在处理中（task_id: {task_id}）。"
                "可稍后用 get_generation_result 查询结果。",
                f"Generation task still running (task_id: {task_id}). "
                "Query later with get_generation_result.",
            )
        )


def generation_timeout_sec() -> int:
    """Global timeout for image/audio generation tasks."""
    return config_generation_timeout_sec("image")


def video_generation_timeout_sec() -> int:
    return config_generation_timeout_sec("video")


def max_video_duration() -> int:
    return config_max_video_duration()
