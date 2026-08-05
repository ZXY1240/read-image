"""Audio understanding and transcription via DashScope.

Two paths:
- ``transcribe_audio``: paraformer-v2 offline ASR (async task). Cheap
  (0.288 yuan/hour, 10h/month free), best for long audio.
- ``analyze_audio``: qwen3.5-omni streaming understanding (must stream=True).
  Fine-grained (tone, effects, mixed content) but audio tokens cost ~8x text;
  prefer transcription for plain speech.

Both accept local paths (uploaded to temporary oss:// storage) or URLs.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from omnimodal.config import (
    DEFAULT_AUDIO_TASK,
    api_key,
    asr_model_name,
    asr_timeout_sec,
    audio_model,
    audio_understanding_max_sec,
)
from omnimodal.errors import ReadImageError, tr
from omnimodal.generation import GenerationClient, GenerationSpec
from omnimodal.http import http_client
from omnimodal.profiles import audio_timeout_for_mode, profile_for_mode
from omnimodal.upload import get_temporary_url
from omnimodal.urls import validate_remote_url

ASR_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
ASR_TIMEOUT_SEC = asr_timeout_sec()

OMNI_MODEL = audio_model()
OMNI_MODEL_PLUS = audio_model("pro")
OMNI_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# base64 input limit for omni input_audio
OMNI_BASE64_MAX_BYTES = 10 * 1024 * 1024
# Practical limit: beyond this, transcription is far cheaper than omni.
OMNI_PRACTICAL_MAX_SEC = audio_understanding_max_sec()

_AUDIO_MIME_BY_SUFFIX = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".amr": "audio/amr",
    ".wma": "audio/x-ms-wma",
}

_AUDIO_FORMATS = set(_AUDIO_MIME_BY_SUFFIX)


def _mime_for_audio(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return _AUDIO_MIME_BY_SUFFIX.get(suffix, "audio/mpeg")


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _audio_format(path_or_url: str) -> str:
    """Return the format name expected by Qwen-Omni ``input_audio``."""
    if path_or_url.startswith("data:"):
        mime = path_or_url[len("data:") :].split(";", 1)[0].lower().strip()
        for suffix, candidate in _AUDIO_MIME_BY_SUFFIX.items():
            if candidate == mime:
                return suffix.lstrip(".")
    suffix = Path(urlparse(path_or_url).path).suffix.lower()
    if suffix in _AUDIO_FORMATS:
        return suffix.lstrip(".")
    return "mp3"


def _ffmpeg_executable() -> str:
    import imageio_ffmpeg

    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise ReadImageError(
            tr(
                f"音频处理依赖 FFmpeg 不可用：{exc}",
                f"FFmpeg dependency unavailable: {exc}",
            )
        ) from exc


def _audio_duration_sec(path_or_url: str) -> int | None:
    if _is_http_url(path_or_url):
        return None
    path = Path(path_or_url)
    if not path.is_file():
        return None
    try:
        result = subprocess.run(
            [
                _ffmpeg_executable(),
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(float(hours) * 3600 + float(minutes) * 60 + float(seconds))


def _to_api_url(path_or_url: str, model: str) -> str:
    """Return a URL accepted by DashScope (public URL or oss://)."""
    if _is_http_url(path_or_url):
        return path_or_url
    return get_temporary_url(path_or_url, model, content_type=_mime_for_audio(path_or_url))


def _audio_content_item(path_or_url: str, model: str) -> dict[str, Any]:
    """Build the omni ``input_audio`` content item for a local path or URL.

    Qwen-Omni expects ``input_audio.data`` plus ``input_audio.format``.
    - http(s) URL → ``data`` is the URL
    - local file ≤10MB → ``data`` is a ``data:audio/...;base64,`` URI
    - local file >10MB → upload to temporary oss:// storage and pass the URL
    - data URL / existing ``oss://`` URL → normalized pass-through
    """
    format_name = _audio_format(path_or_url)
    if path_or_url.startswith("data:"):
        return {
            "type": "input_audio",
            "input_audio": {"data": path_or_url, "format": format_name},
        }
    if _is_http_url(path_or_url):
        return {
            "type": "input_audio",
            "input_audio": {"data": path_or_url, "format": format_name},
        }
    if Path(path_or_url).is_file():
        data = base64.b64encode(Path(path_or_url).read_bytes()).decode("ascii")
        if len(data) <= OMNI_BASE64_MAX_BYTES:
            return {
                "type": "input_audio",
                "input_audio": {
                    "data": f"data:{_mime_for_audio(path_or_url)};base64,{data}",
                    "format": format_name,
                },
            }
        url = get_temporary_url(path_or_url, model, content_type=_mime_for_audio(path_or_url))
        return {
            "type": "input_audio",
            "input_audio": {"data": url, "format": format_name},
        }
    # oss:// 或未知形式：按 URL 透传，同时给出格式名
    return {
        "type": "input_audio",
        "input_audio": {"data": path_or_url, "format": format_name},
    }


def _transcribe_qwen_asr(
    path_or_url: str,
    language: str = "zh",
    model: str | None = None,
) -> dict[str, Any]:
    """Transcribe short audio with qwen3-asr-flash (synchronous chat API)."""
    selected_model = model or asr_model_name()
    if _is_http_url(path_or_url) or path_or_url.startswith("oss://"):
        audio_data = path_or_url
    else:
        path = Path(path_or_url)
        if not path.is_file():
            raise ReadImageError(
                tr(
                    "音频文件不存在。",
                    "Audio file does not exist.",
                )
            )
        raw = path.read_bytes()
        if len(raw) > 10 * 1024 * 1024:
            raise ReadImageError(
                tr(
                    "qwen3-asr-flash 仅支持 10MB 以内的音频。",
                    "qwen3-asr-flash supports audio files up to 10MB.",
                )
            )
        encoded = base64.b64encode(raw).decode("ascii")
        audio_data = f"data:{_mime_for_audio(path_or_url)};base64,{encoded}"

    payload: dict[str, Any] = {
        "model": selected_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_data},
                    }
                ],
            }
        ],
        "stream": False,
        "asr_options": {"language": language, "enable_itn": False},
    }
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }
    if audio_data.startswith("oss://"):
        headers["X-DashScope-OssResourceResolve"] = "enable"
    try:
        response = http_client.post(
            OMNI_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=max(120, asr_timeout_sec()),
        )
    except Exception as exc:
        raise ReadImageError(
            tr(
                "Qwen-ASR 请求失败（网络错误）。",
                "Qwen-ASR request failed (network error).",
            )
        ) from exc
    if response.status_code >= 400:
        raise ReadImageError(
            tr(
                f"Qwen-ASR 请求失败（HTTP {response.status_code}）。",
                f"Qwen-ASR request failed (HTTP {response.status_code}).",
            )
            + f" {response.text[:300]}"
        )
    try:
        parsed = response.json()
    except json.JSONDecodeError as exc:
        raise ReadImageError(
            tr(
                "Qwen-ASR 返回了非 JSON 响应。",
                "Qwen-ASR returned a non-JSON response.",
            )
        ) from exc
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ReadImageError(
            tr(
                "Qwen-ASR 响应缺少识别结果。",
                "Qwen-ASR response is missing transcription results.",
            )
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ReadImageError(
            tr(
                "Qwen-ASR 返回的识别文本为空。",
                "Qwen-ASR returned empty transcription text.",
            )
        )
    return {"text": text.strip(), "status": "SUCCEEDED"}


def _download_transcription_result(transcription_url: str) -> str:
    """Download and parse the JSON result file returned by Fun-ASR/Paraformer."""
    validate_remote_url(transcription_url)
    try:
        response = http_client.get(transcription_url, timeout=120)
    except Exception as exc:
        raise ReadImageError(
            tr(
                "下载转写结果失败。",
                "Failed to download transcription result.",
            )
        ) from exc
    if response.status_code >= 400:
        raise ReadImageError(
            tr(
                f"下载转写结果失败（HTTP {response.status_code}）。",
                f"Failed to download transcription result (HTTP {response.status_code}).",
            )
        )
    try:
        parsed = response.json()
    except json.JSONDecodeError as exc:
        raise ReadImageError(
            tr(
                "转写结果文件不是有效 JSON。",
                "Transcription result is not valid JSON.",
            )
        ) from exc
    transcripts = parsed.get("transcripts") if isinstance(parsed, dict) else None
    if not isinstance(transcripts, list):
        return ""
    texts: list[str] = []
    for item in transcripts:
        if not isinstance(item, dict):
            continue
        value = item.get("text") or item.get("transcript")
        if isinstance(value, str) and value.strip():
            texts.append(value.strip())
    return "\n".join(texts)


# ---------------- transcription (paraformer, async task) ----------------


def transcribe_audio(
    path_or_url: str,
    language: str = "zh",
    model: str | None = None,
    wait: bool = True,
    progress_cb=None,
) -> dict[str, Any]:
    """Transcribe an audio file to text.

    Args:
        path_or_url: local path or http(s) URL.
        language: hint (zh/en/ja/yue/ko/de/fr/ru).
        model: paraformer model name.
        wait: if False, return immediately with task_id for later query.
        progress_cb: optional callback for progress.

    Returns:
        dict with ``text`` (when wait=True) or ``task_id``/``status``.
    """
    selected_model = model or asr_model_name()
    if selected_model.lower().startswith("qwen3-asr-flash"):
        return _transcribe_qwen_asr(path_or_url, language=language, model=selected_model)
    audio_url = _to_api_url(path_or_url, selected_model)
    payload = {
        "model": selected_model,
        "input": {"file_urls": [audio_url]},
        "parameters": {"language_hints": [language]},
    }
    extra_headers: dict[str, str] = {}
    if audio_url.startswith("oss://"):
        extra_headers["X-DashScope-OssResourceResolve"] = "enable"
    client = GenerationClient(
        GenerationSpec(
            endpoint=ASR_ENDPOINT,
            model=selected_model,
            poll_interval=5,
            timeout_sec=asr_timeout_sec(),
        ),
        extra_headers=extra_headers,
    )
    task_id = client.submit(payload)
    if not wait:
        return {"task_id": task_id, "status": "PENDING"}
    data = client.wait_for_result(task_id, progress_cb=progress_cb)
    results = data.get("output", {}).get("results")
    if not isinstance(results, list) or not results:
        raise ReadImageError(
            tr(
                "转写任务成功但结果为空。",
                "Transcription succeeded but returned no results.",
            )
        )
    text_parts: list[str] = []
    failures: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("subtask_status") == "FAILED":
            failures.append(str(result.get("message") or result.get("code") or "unknown"))
            continue
        direct = result.get("text") or result.get("transcript")
        if isinstance(direct, str) and direct.strip():
            text_parts.append(direct.strip())
        transcription_url = result.get("transcription_url")
        if isinstance(transcription_url, str) and transcription_url:
            downloaded = _download_transcription_result(transcription_url)
            if downloaded:
                text_parts.append(downloaded)
    if not text_parts:
        if failures:
            raise ReadImageError(
                tr(
                    f"转写任务失败：{'; '.join(failures)}",
                    f"Transcription failed: {'; '.join(failures)}",
                )
            )
        raise ReadImageError(
            tr(
                "转写任务成功但结果为空。",
                "Transcription succeeded but returned no results.",
            )
        )
    text = "\n".join(text_parts)
    return {"text": text, "task_id": task_id, "status": "SUCCEEDED"}


def asr_task_status(task_id: str) -> dict[str, Any]:
    """Query a previously submitted ASR task by task_id."""
    client = GenerationClient(GenerationSpec(endpoint=ASR_ENDPOINT, model=asr_model_name()))
    data = client.poll_status(task_id)
    status = data.get("output", {}).get("task_status")
    if status == "SUCCEEDED":
        results = data.get("output", {}).get("results")
        text_parts: list[str] = []
        for result in results if isinstance(results, list) else []:
            if not isinstance(result, dict):
                continue
            direct = result.get("text") or result.get("transcript")
            if isinstance(direct, str) and direct.strip():
                text_parts.append(direct.strip())
            transcription_url = result.get("transcription_url")
            if isinstance(transcription_url, str) and transcription_url:
                text_parts.append(_download_transcription_result(transcription_url))
        text = "\n".join(part for part in text_parts if part)
        return {"task_id": task_id, "status": "SUCCEEDED", "text": text}
    return {"task_id": task_id, "status": status}


# ---------------- understanding (qwen3.5-omni, streaming) ----------------


def analyze_audio(
    path_or_url: str,
    task: str = "详细描述这段音频的内容",
    mode: str | None = None,
    tier: str = "standard",
    model: str | None = None,
) -> str:
    """Understand audio content via qwen3.5-omni (streaming, must stream=True).

    Args:
        path_or_url: local path or URL.
        task: what to extract from the audio.
        mode: omnimodal recognition mode (quick/standard/...), affects thinking & timeout.
        tier: standard -> omni-flash, pro -> omni-plus.

    Returns:
        Model's text answer.
    """
    model = model or audio_model("pro" if tier == "pro" else "standard")
    content_item = _audio_content_item(path_or_url, model)

    profile = profile_for_mode(mode)
    payload: dict[str, Any] = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": profile.system_prompt},
            {"role": "user", "content": [{"type": "text", "text": task}, content_item]},
        ],
    }
    if profile.max_tokens is not None:
        payload["max_tokens"] = profile.max_tokens

    timeout_sec = max(audio_timeout_for_mode(mode), 90)
    headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }
    collected: list[str] = []
    try:
        with http_client.stream(
            "POST", OMNI_ENDPOINT, headers=headers, json=payload, timeout=timeout_sec
        ) as response:
            if response.status_code >= 400:
                body = response.read().decode("utf-8", errors="replace")
                raise ReadImageError(
                    tr(
                        f"音频理解请求失败（HTTP {response.status_code}）。",
                        f"Audio understanding failed (HTTP {response.status_code}).",
                    )
                    + f" {body[:300]}"
                )
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:") :].strip()
                if chunk == "[DONE]":
                    break
                try:
                    parsed = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                try:
                    delta = parsed["choices"][0]["delta"]
                except (KeyError, IndexError, TypeError):
                    continue
                content = delta.get("content")
                if isinstance(content, str) and content:
                    collected.append(content)
    except ReadImageError:
        raise
    except Exception as exc:
        raise ReadImageError(
            tr(
                "音频理解网络调用失败。",
                "Audio understanding network call failed.",
            )
        ) from exc

    if not collected:
        raise ReadImageError(
            tr(
                "音频理解返回为空。",
                "Audio understanding returned empty.",
            )
        )
    return "".join(collected).strip()


def audio_should_transcribe(path_or_url: str, duration_sec: int | None = None) -> bool:
    """Heuristic: for long audio, transcription is far cheaper than omni."""
    if duration_sec is None:
        duration_sec = _audio_duration_sec(path_or_url)
    if duration_sec is not None:
        return duration_sec > OMNI_PRACTICAL_MAX_SEC
    path = Path(path_or_url)
    return path.is_file() and path.stat().st_size > 10 * 1024 * 1024


def recognize_audio(
    path_or_url: str,
    task: str = DEFAULT_AUDIO_TASK,
    mode: str | None = None,
    tier: str = "standard",
    wait: bool = True,
) -> str | dict[str, Any]:
    """Auto-route audio recognition: understanding for short clips, ASR for long files."""
    if audio_should_transcribe(path_or_url):
        return transcribe_audio(
            path_or_url,
            wait=wait,
        )
    return analyze_audio(path_or_url, task=task, mode=mode, tier=tier)
