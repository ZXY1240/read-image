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

import json
from pathlib import Path
from typing import Any

from omnimodal.config import api_key
from omnimodal.errors import ReadImageError, tr
from omnimodal.generation import GenerationClient, GenerationSpec
from omnimodal.http import http_client
from omnimodal.profiles import profile_for_mode
from omnimodal.upload import get_temporary_url

ASR_MODEL = "paraformer-v2"
ASR_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
ASR_TIMEOUT_SEC = 1800  # 30 min for long files

OMNI_MODEL = "qwen3.5-omni-flash"
OMNI_MODEL_PLUS = "qwen3.5-omni-plus"
OMNI_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# base64 input limit for omni input_audio
OMNI_BASE64_MAX_BYTES = 10 * 1024 * 1024
# Practical limit: beyond this, transcription is far cheaper than omni.
OMNI_PRACTICAL_MAX_SEC = 180  # 3 min

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


def _mime_for_audio(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return _AUDIO_MIME_BY_SUFFIX.get(suffix, "audio/mpeg")


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _to_api_url(path_or_url: str, model: str) -> str:
    """Return a URL accepted by DashScope (public URL or oss://)."""
    if _is_http_url(path_or_url):
        return path_or_url
    return get_temporary_url(path_or_url, model, content_type=_mime_for_audio(path_or_url))


# ---------------- transcription (paraformer, async task) ----------------

def transcribe_audio(
    path_or_url: str,
    language: str = "zh",
    model: str = ASR_MODEL,
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
    audio_url = _to_api_url(path_or_url, ASR_MODEL)
    payload = {
        "model": model,
        "input": {"file_urls": [audio_url]},
        "parameters": {"language_hints": [language]},
    }
    client = GenerationClient(
        GenerationSpec(
            endpoint=ASR_ENDPOINT,
            model=model,
            poll_interval=5,
            timeout_sec=ASR_TIMEOUT_SEC,
        )
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
    text = "\n".join(
        str(r.get("text", ""))
        for r in results
        if isinstance(r, dict) and r.get("text")
    )
    return {"text": text, "task_id": task_id, "status": "SUCCEEDED"}


def asr_task_status(task_id: str) -> dict[str, Any]:
    """Query a previously submitted ASR task by task_id."""
    client = GenerationClient(
        GenerationSpec(endpoint=ASR_ENDPOINT, model=ASR_MODEL)
    )
    data = client.poll_status(task_id)
    status = data.get("output", {}).get("task_status")
    if status == "SUCCEEDED":
        results = data.get("output", {}).get("results")
        text = "\n".join(
            str(r.get("text", ""))
            for r in results
            if isinstance(r, dict) and r.get("text")
        )
        return {"task_id": task_id, "status": "SUCCEEDED", "text": text}
    return {"task_id": task_id, "status": status}


# ---------------- understanding (qwen3.5-omni, streaming) ----------------

def analyze_audio(
    path_or_url: str,
    task: str = "详细描述这段音频的内容",
    mode: str | None = None,
    tier: str = "standard",
) -> str:
    """Understand audio content via qwen3.5-omni (streaming, must stream=True).

    Args:
        path_or_url: local path or URL.
        task: what to extract from the audio.
        mode: read-image mode (quick/standard/...), affects thinking & timeout.
        tier: standard -> omni-flash, pro -> omni-plus.

    Returns:
        Model's text answer.
    """
    model = OMNI_MODEL_PLUS if tier == "pro" else OMNI_MODEL
    audio_url = _to_api_url(path_or_url, model)

    # omni accepts base64 (<10MB) or URL; prefer URL for local files we already
    # uploaded, but allow direct base64 for small files to skip the upload hop.
    content_item: dict[str, Any]
    if not _is_http_url(audio_url):
        content_item = {
            "type": "input_audio",
            "input_audio": {"data": f"data:;base64,{audio_url}"},
        }
    else:
        content_item = {
            "type": "input_audio",
            "input_audio": {"url": audio_url},
        }

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

    timeout_sec = max(profile.timeout_sec, 90)
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
                chunk = line[len("data:"):].strip()
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
    if duration_sec is not None:
        return duration_sec > OMNI_PRACTICAL_MAX_SEC
    return False
