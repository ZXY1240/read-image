"""Upload local media files to DashScope temporary storage (oss:// URLs).

DashScope's async generation APIs (Wanx, Paraformer) accept public URLs or
``oss://`` temporary URLs, not local paths. This module implements the
two-step flow: request an OSS upload policy, then POST the file, returning an
``oss://`` URL that callers pass as ``file_urls`` / ``img_url`` / ``audio_url``.

Temporary files expire after 48 hours.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omnimodal.config import api_key
from omnimodal.errors import ReadImageError, tr
from omnimodal.http import http_client

_UPLOAD_POLICY_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/uploads"
_UPLOAD_TIMEOUT_SEC = 60


def _upload_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key()}",
    }
    if extra:
        headers.update(extra)
    return headers


def _request_upload_policy(model: str) -> dict[str, Any]:
    """Request an OSS upload policy for the given model.

    Returns the parsed policy dict (upload_host, policy, signature, ...).
    """
    payload = {"action": "getPolicy", "model": model}
    try:
        response = http_client.get(
            _UPLOAD_POLICY_ENDPOINT,
            params=payload,
            headers=_upload_headers(),
            timeout=_UPLOAD_TIMEOUT_SEC,
        )
    except Exception as exc:
        raise ReadImageError(
            tr(
                "请求上传凭证失败。",
                "Failed to request upload policy.",
            )
        ) from exc
    if response.status_code >= 400:
        raise ReadImageError(
            tr(
                f"请求上传凭证失败（HTTP {response.status_code}）。",
                f"Failed to request upload policy (HTTP {response.status_code}).",
            )
        )
    try:
        parsed = response.json()
    except json.JSONDecodeError as exc:
        raise ReadImageError(
            tr(
                "请求上传凭证返回了非 JSON 响应。",
                "Upload policy response is not JSON.",
            )
        ) from exc
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, dict) or "upload_host" not in data:
        raise ReadImageError(
            tr(
                "上传凭证缺少 upload_host。",
                "Upload policy is missing upload_host.",
            )
        )
    return data


def _upload_form(
    upload_host: str,
    file_path: Path,
    policy: dict[str, Any],
    key: str,
    content_type: str,
) -> None:
    """POST the file as multipart form data to the OSS upload host."""
    fields = {
        "policy": str(policy.get("policy", "")),
        "Signature": str(policy.get("signature", "")),
        "OSSAccessKeyId": str(policy.get("oss_access_key_id", "")),
        "x-oss-object-acl": str(policy.get("x_oss_object_acl", "")),
        "x-oss-forbid-overwrite": str(policy.get("x_oss_forbid_overwrite", "")),
        "key": key,
        "success_action_status": "200",
    }
    try:
        with file_path.open("rb") as f:
            response = http_client.post(
                upload_host,
                data=fields,
                files={"file": (file_path.name, f, content_type)},
                timeout=_UPLOAD_TIMEOUT_SEC,
            )
    except Exception as exc:
        raise ReadImageError(
            tr(
                "上传文件失败。",
                "Failed to upload file.",
            )
        ) from exc
    if response.status_code >= 400:
        raise ReadImageError(
            tr(
                f"上传文件失败（HTTP {response.status_code}）。",
                f"Failed to upload file (HTTP {response.status_code}).",
            )
        )


def get_temporary_url(path: str, model: str, content_type: str = "application/octet-stream") -> str:
    """Upload a local file to DashScope temporary storage and return an oss:// URL.

    Args:
        path: local file path.
        model: the model the file will be used with (e.g. ``paraformer-v2``).
        content_type: MIME type of the file.

    Returns:
        An ``oss://`` URL. Valid for 48 hours.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ReadImageError(
            tr(
                "文件不存在。",
                "File does not exist.",
            )
        )
    policy = _request_upload_policy(model)
    upload_host = str(policy["upload_host"])
    upload_dir = str(policy.get("upload_dir", "omnimodal"))
    key = f"{upload_dir.rstrip('/')}/{file_path.name}"
    _upload_form(upload_host, file_path, policy, key, content_type)
    return f"oss://{key}"
