"""MCP server for generation capabilities (image/video/audio + ASR).

Separate process from the vision server so long-running generation tasks
(1-5 min video) never block recognition. Exposes:

- omnimodal_generate_image(prompt, ...)
- omnimodal_generate_video(prompt, ...)
- omnimodal_generate_video_from_image(image, ...)
- omnimodal_edit_video(video, ...)
- omnimodal_generate_audio(text, ...)
- omnimodal_transcribe_audio(audio, ...)
- omnimodal_get_task_result(task_id)

Tier selection (standard/pro/max) maps to model gradients; default tier is
configurable via OMNIMODAL_DEFAULT_TIER. Cost confirmation is mandatory.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import sys
import time
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from omnimodal import audio_processing
from omnimodal.config import (
    api_key,
    audio_generation_model,
    default_tier,
    generation_output_dir,
    generation_timeout_sec,
    image_generation_model,
    max_video_duration,
    video_generation_model,
)
from omnimodal.errors import ReadImageError, tr
from omnimodal.generation import (
    GenerationClient,
    GenerationSpec,
    GenerationTimeoutError,
    _auth_headers,
    video_generation_timeout_sec,
)
from omnimodal.http import http_client
from omnimodal.paths import ensure_allowed_output_dir
from omnimodal.upload import get_temporary_url

mcp = FastMCP("omnimodal-generation")

# ---- shared helpers -------------------------------------------------------

_DASH_REST = "https://dashscope.aliyuncs.com/api/v1/services"
T2I_ENDPOINT = f"{_DASH_REST}/aigc/text2image/image-synthesis"
# qwen-image 系列（qwen-image-2.0 等）走 multimodal-generation：Chat 消息格式、
# 同步返回 output.choices[].message.content[].image，不是异步任务。
T2I_CHAT_ENDPOINT = f"{_DASH_REST}/aigc/multimodal-generation/generation"
T2V_ENDPOINT = f"{_DASH_REST}/aigc/video-generation/video-synthesis"
TTS_ENDPOINT = f"{_DASH_REST}/audio/tts/SpeechSynthesizer"
VOICE_ENDPOINT = f"{_DASH_REST}/audio/tts/SpeechSynthesizer"
VOICE_CUSTOMIZATION_ENDPOINT = f"{_DASH_REST}/audio/tts/customization"
MUSIC_ENDPOINT = f"{_DASH_REST}/audio/music/generation"
ASR_ENDPOINT = audio_processing.ASR_ENDPOINT

QWEN_TTS_VC_MODEL = "qwen3-tts-vc-2026-01-22"
QWEN_TTS_VD_MODEL = "qwen3-tts-vd-2026-01-26"


def _default_tier() -> str:
    return default_tier()


def _output_dir() -> Path:
    """Resolve and validate the generation output directory."""
    configured = str(generation_output_dir())
    allowed = ensure_allowed_output_dir(configured or None)  # raises if not allowed
    return Path(allowed) if allowed else generation_output_dir()


def _format_cost(spec: GenerationSpec, detail: str) -> str:
    return f"{detail}（费用{spec.cost_label}）" if spec.price_hint else detail


def _t2i_spec(tier: str) -> GenerationSpec:
    model = image_generation_model(tier)
    price = {"standard": 0.18, "pro": 0.50, "max": 0.25}.get(tier, 0.18)
    return GenerationSpec(
        endpoint=T2I_CHAT_ENDPOINT,
        model=model,
        poll_interval=10,
        timeout_sec=generation_timeout_sec("image"),
        price_hint=price,
        price_unit=tr("张", "image"),
    )


def _t2v_spec(tier: str) -> GenerationSpec:
    model = video_generation_model(tier)
    price = {"standard": 0.60, "pro": 1.00, "max": 1.20}.get(tier, 0.60)
    return GenerationSpec(
        endpoint=T2V_ENDPOINT,
        model=model,
        poll_interval=15,
        timeout_sec=generation_timeout_sec("video"),
        price_hint=price,
        price_unit=tr("秒", "sec"),
    )


def _tts_spec(tier: str) -> GenerationSpec:
    model = audio_generation_model(tier)
    price = {"standard": 0.80, "pro": 1.50, "max": 1.00}.get(tier, 0.80)
    return GenerationSpec(
        endpoint=TTS_ENDPOINT,
        model=model,
        poll_interval=10,
        timeout_sec=generation_timeout_sec("audio"),
        price_hint=price,
        price_unit=tr("万字符", "10k chars"),
    )


def _tts_request(model: str, text: str, voice: str) -> tuple[str, dict[str, Any]]:
    """Return the endpoint and payload for a non-streaming TTS request."""
    if model.lower().startswith(("qwen3-tts-vc", "qwen3-tts-vd")):
        if not voice or voice == "auto":
            raise ReadImageError(
                tr(
                    "该模型必须先创建声音再合成，请提供 voice_id。",
                    "This model requires a custom voice id before synthesis.",
                )
            )
        return T2I_CHAT_ENDPOINT, {
            "model": model,
            "input": {
                "text": text,
                "voice": voice,
            },
        }
    if model.lower().startswith("qwen3-tts-instruct"):
        selected_voice = voice if voice and voice != "auto" else "Cherry"
        return T2I_CHAT_ENDPOINT, {
            "model": model,
            "input": {
                "text": text,
                "voice": selected_voice,
                "language_type": "Chinese",
                "instructions": "语速适中，语气自然，发音清晰。",
            },
        }
    if model.lower().startswith("cosyvoice-v3.5"):
        if not voice or voice == "auto":
            raise ReadImageError(
                tr(
                    "cosyvoice-v3.5 不支持系统音色，请先创建声音复刻/设计音色并提供 voice 参数。",
                    "cosyvoice-v3.5 has no system voices; create a cloned/designed voice "
                    "and pass its voice id.",
                )
            )
        selected_voice = voice
    else:
        selected_voice = voice if voice and voice != "auto" else "longanhuan_v3.6"
    return TTS_ENDPOINT, {
        "model": model,
        "input": {
            "text": text,
            "voice": selected_voice,
            "format": "mp3",
            "sample_rate": 24000,
        },
    }


def _create_custom_voice(
    voice_prompt: str,
    target_model: str,
    prefix: str = "omnimodal",
    audio: str | None = None,
    model: str | None = None,
) -> str:
    """Create a custom voice via the DashScope customization API."""
    selected_model = model or (
        "voice-enrollment"
        if target_model.lower().startswith(("qwen-audio", "cosyvoice"))
        else "qwen-voice-design"
    )
    input_payload: dict[str, Any] = {
        "action": "create_voice" if selected_model == "voice-enrollment" else "create",
        "target_model": target_model,
    }
    if selected_model == "qwen-voice-enrollment":
        if not audio:
            raise ReadImageError(
                tr(
                    "声音克隆必须提供参考音频。",
                    "Voice cloning requires a reference audio file.",
                )
            )
        audio_source = audio
        if not audio.startswith(("http://", "https://", "oss://", "data:")):
            audio_path = Path(audio)
            if not audio_path.is_file():
                raise ReadImageError(
                    tr(
                        f"音频文件不存在：{audio_path}",
                        f"Audio file does not exist: {audio_path}",
                    )
                )
            mime = mimetypes.guess_type(audio_path.name)[0] or "audio/mpeg"
            encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
            audio_source = f"data:{mime};base64,{encoded}"
        input_payload.update(
            {
                "preferred_name": prefix,
                "audio": {
                    "data": audio_source,
                },
            }
        )
    elif selected_model == "qwen-voice-design":
        input_payload.update(
            {
                "preferred_name": prefix,
                "voice_prompt": voice_prompt,
                "preview_text": "各位听众朋友大家好，欢迎收听本期节目。",
                "language": "zh",
            }
        )
    else:
        input_payload.update(
            {
                "voice_prompt": voice_prompt,
                "preview_text": "各位听众朋友大家好，欢迎收听本期节目。",
                "prefix": prefix,
                "language_hints": ["zh"],
            }
        )
        if audio:
            input_payload["url"] = _audio_source_for_api(audio, selected_model)
    payload = {
        "model": selected_model,
        "input": input_payload,
        "parameters": {"sample_rate": 24000, "response_format": "wav"},
    }
    response = http_client.post(
        VOICE_CUSTOMIZATION_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180.0,
    )
    if response.status_code >= 400:
        raise ReadImageError(
            tr(
                f"创建自定义音色失败（HTTP {response.status_code}）：{response.text[:300]}",
                f"Failed to create custom voice (HTTP {response.status_code}): "
                f"{response.text[:300]}",
            )
        )
    try:
        output = response.json().get("output", {})
    except json.JSONDecodeError as exc:
        raise ReadImageError(
            tr(
                "创建自定义音色返回了非 JSON 响应。",
                "Custom voice creation returned a non-JSON response.",
            )
        ) from exc
    voice_id = output.get("voice_id") or output.get("voice")
    if isinstance(voice_id, dict):
        voice_id = voice_id.get("voice_id") or voice_id.get("id")
    if not isinstance(voice_id, str) or not voice_id:
        raise ReadImageError(
            tr(
                "创建自定义音色成功但响应缺少音色 ID。",
                "Custom voice creation succeeded but response is missing voice id.",
            )
        )
    return voice_id


def _audio_source_for_api(value: str, model: str) -> str:
    """Normalize a local audio path to a DashScope-readable URL/data URI."""
    if value.startswith(("http://", "https://", "oss://", "data:")):
        return value
    path = Path(value)
    if not path.is_file():
        raise ReadImageError(
            tr(
                f"音频文件不存在：{path}",
                f"Audio file does not exist: {path}",
            )
        )
    mime = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
    return get_temporary_url(str(path), model, content_type=mime)


def _media_source_for_generation(
    value: str,
    model: str,
    default_mime: str = "application/octet-stream",
) -> str:
    """Normalize a local image/video path to an OSS URL for generation APIs."""
    if value.startswith(("http://", "https://", "oss://", "data:")):
        return value
    path = Path(value)
    if not path.is_file():
        raise ReadImageError(
            tr(
                f"媒体文件不存在：{path}",
                f"Media file does not exist: {path}",
            )
        )
    mime = mimetypes.guess_type(path.name)[0] or default_mime
    return get_temporary_url(str(path), model, content_type=mime)


def _submit_audio_sync(endpoint: str, payload: dict[str, Any], model: str) -> Path:
    """Post a synchronous audio generation request and download the result."""
    try:
        response = http_client.post(
            endpoint,
            headers=_auth_headers(),
            json=payload,
            timeout=120.0,
        )
    except Exception as exc:
        raise ReadImageError(
            tr(
                "音频生成请求失败（网络错误）。",
                "Audio generation request failed (network error).",
            )
        ) from exc
    if response.status_code >= 400:
        raise ReadImageError(
            tr(
                f"音频生成请求失败（HTTP {response.status_code}）。",
                f"Audio generation failed (HTTP {response.status_code}).",
            )
            + f" {response.text[:300]}"
        )
    try:
        parsed = response.json()
        output = parsed.get("output", {}) if isinstance(parsed, dict) else {}
        audio = output.get("audio") if isinstance(output, dict) else None
        audio_url: Any = None
        if isinstance(audio, dict):
            audio_url = audio.get("url")
        if audio_url is None:
            audio_url = output.get("audio_url")
        if audio_url is None:
            results = output.get("results") if isinstance(output, dict) else None
            if isinstance(results, list) and results and isinstance(results[0], dict):
                audio_url = results[0].get("url") or results[0].get("audio_url")
    except (json.JSONDecodeError, AttributeError, KeyError):
        audio_url = None
    if not audio_url:
        raise ReadImageError(
            tr(
                "音频生成响应缺少音频 URL。",
                "Audio generation response missing audio URL.",
            )
        )
    client = GenerationClient(
        GenerationSpec(
            endpoint=endpoint,
            model=model,
            timeout_sec=generation_timeout_sec("audio"),
        ),
        output_dir=str(_output_dir()),
    )
    return _download(client, str(audio_url))


def _music_payload(prompt: str) -> dict[str, Any]:
    """Build a fun-music-v1 payload; gender is inferred from the prompt."""
    input_payload: dict[str, Any] = {"prompt": prompt}
    lowered = prompt.lower()
    if any(word in lowered for word in ("男声", "男歌手", "男低音", "男中音", "男高音", "male")):
        input_payload["gender"] = "male"
    elif any(
        word in lowered for word in ("女声", "女歌手", "女低音", "女中音", "女高音", "female")
    ):
        input_payload["gender"] = "female"
    return {
        "model": "fun-music-v1",
        "input": input_payload,
    }


def _video_resolution(value: str) -> str:
    """Normalize video generation resolution; DashScope only accepts 720P/1080P."""
    normalized = str(value or "").upper()
    if normalized == "1080P":
        return "1080P"
    return "720P"


def _submit_video_task(
    spec: GenerationSpec,
    payload: dict[str, Any],
    wait: bool,
    ctx: Context | None,
    cost: str,
) -> dict[str, Any]:
    """Submit a video task and optionally wait/download the result."""
    client = GenerationClient(
        spec,
        output_dir=str(_output_dir()),
        extra_headers={"X-DashScope-OssResourceResolve": "enable"},
    )
    task_id = client.submit(payload)
    if not wait:
        return {"task_id": task_id, "status": "PENDING", "cost": cost}
    try:
        data = client.wait_for_result(task_id, progress_cb=_report(ctx))
    except GenerationTimeoutError as exc:
        return {"task_id": exc.task_id, "status": "RUNNING", "cost": cost, "note": str(exc)}
    video_url = data.get("output", {}).get("video_url")
    if not video_url:
        raise ReadImageError(tr("视频任务成功但缺少 video_url。", "Video task missing video_url."))
    saved = _download(client, video_url)
    return {"status": "SUCCEEDED", "files": [str(saved)], "cost": cost}


# ---- tools ----------------------------------------------------------------


def _generate_image_chat_sync(
    prompt: str,
    spec: GenerationSpec,
    size: str,
    n: int,
    image: str | None = None,
) -> list[Path]:
    """文生图（multimodal-generation）：Chat 格式、同步返回。

    qwen-image-2.0 与 wan2.7-image-pro 都走此端点（已真实调用确认）。
    响应结构：output.choices[].message.content[].image（URL 或 base64 data URL）。
    size 用档位（如 1K/2K/4K）或 "1024*1024"（qwen-image 支持像素）。
    """
    content: list[dict[str, Any]] = [{"text": prompt}]
    if image:
        content.append({"image": _image_edit_source(image)})
    parameters: dict[str, Any] = {"n": n, "watermark": False}
    if size:
        parameters["size"] = size
    payload: dict[str, Any] = {
        "model": spec.model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ]
        },
        "parameters": parameters,
    }
    try:
        response = http_client.post(
            spec.endpoint,
            headers=_auth_headers(),
            json=payload,
            timeout=120.0,
        )
    except Exception as exc:
        raise ReadImageError(
            tr(
                "文生图请求失败（网络错误）。",
                "Image generation failed (network error).",
            )
        ) from exc
    if response.status_code >= 400:
        raise ReadImageError(
            tr(
                "文生图请求失败（HTTP {code}）：{detail}",
                "Image generation failed (HTTP {code}): {detail}",
            ).format(
                code=response.status_code,
                detail=response.text[:300],
            )
        )
    try:
        parsed = response.json()
    except json.JSONDecodeError as exc:
        raise ReadImageError(
            tr(
                "文生图返回了非 JSON 响应。",
                "Image generation returned non-JSON response.",
            )
        ) from exc
    choices = parsed.get("output", {}).get("choices", []) if isinstance(parsed, dict) else []
    urls: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        content = choice.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("image"):
                image = item["image"]
                if isinstance(image, str) and image:
                    urls.append(image)
    if not urls:
        raise ReadImageError(
            tr(
                "文生图成功但响应中没有图片。",
                "Image generation succeeded but response has no image.",
            )
        )
    client = GenerationClient(spec, output_dir=str(_output_dir()))
    saved = [
        client.download_result(url, f"t2i-{int(time.time())}-{index}.png")
        for index, url in enumerate(urls)
    ]
    return saved


def _image_edit_source(image: str) -> str:
    """Normalize a local image edit source to a URL/data URI."""
    if image.startswith(("http://", "https://", "oss://", "data:")):
        return image
    path = Path(image)
    if not path.is_file():
        raise ReadImageError(
            tr(
                f"图片编辑源文件不存在：{path}",
                f"Image edit source does not exist: {path}",
            )
        )
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


@mcp.tool(name="omnimodal_generate_image")
async def omnimodal_generate_image(
    prompt: Annotated[str, Field(description="图像描述，≤500 字")],
    image: Annotated[
        str | None,
        Field(
            description="可选：本地图片路径、URL 或 data URL；传入时按图片编辑处理",
            default=None,
        ),
    ] = None,
    tier: Annotated[
        str,
        Field(
            description=(
                "档位: standard(qwen-image-3.0)/pro(wan2.7-image-pro)/max(qwen-image-3.0-pro)"
            ),
            default="standard",
        ),
    ] = "standard",
    size: Annotated[str, Field(description="尺寸如 1024*1024", default="1024*1024")] = "1024*1024",
    n: Annotated[int, Field(description="生成数量 1-4", default=1)] = 1,
    wait: Annotated[
        bool, Field(description="true=等待完成返回结果，false=提交后返回task_id", default=True)
    ] = True,
    confirm: Annotated[
        bool, Field(description="必须为 true 才会实际调用付费生成接口", default=False)
    ] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """文生图。standard 档 qwen-image-3.0，pro 档 wan2.7-image-pro，max 档 qwen-image-3.0-pro。"""
    tier = tier if tier in {"standard", "pro", "max"} else _default_tier()
    if n < 1 or n > 4:
        raise ReadImageError(tr("生成数量 n 必须在 1-4 之间。", "n must be 1-4."))
    spec = _t2i_spec(tier)
    if image:
        spec = GenerationSpec(
            endpoint=T2I_CHAT_ENDPOINT,
            model="qwen-image-edit-max",
            poll_interval=10,
            timeout_sec=generation_timeout_sec("image"),
            price_hint=0.20,
            price_unit=tr("张", "image"),
        )
    cost = f"预计 {spec.price_hint * n:.2f} 元（{n} 张 × {spec.price_hint} 元/张）"
    if not confirm:
        return {
            "status": "NEEDS_CONFIRMATION",
            "cost": cost,
            "note": tr(
                "设置 confirm=true 后才会实际调用付费接口。",
                "Set confirm=true to call the paid generation API.",
            ),
        }
    try:
        saved = await asyncio.to_thread(
            _generate_image_chat_sync,
            prompt,
            spec,
            size,
            n,
            image,
        )
    except ReadImageError:
        raise
    except Exception as exc:
        raise ReadImageError(
            tr(
                "文生图失败：{detail}",
                "Image generation failed: {detail}",
            ).format(detail=str(exc)),
        ) from exc
    return {
        "status": "SUCCEEDED",
        "files": [str(p) for p in saved],
        "cost": cost,
    }


@mcp.tool(name="omnimodal_generate_video")
async def omnimodal_generate_video(
    prompt: Annotated[str, Field(description="视频内容描述")],
    tier: Annotated[
        str,
        Field(
            description=(
                "档位: standard(wan2.7-t2v)/pro(wan2.7-t2v 1080P)/max(happyhorse-1.1-t2v)"
            ),
            default="standard",
        ),
    ] = "standard",
    duration: Annotated[int, Field(description="秒数", default=5)] = 5,
    resolution: Annotated[
        str,
        Field(description="720P/1080P；480P 会自动升级为 720P", default="720P"),
    ] = "720P",
    wait: Annotated[
        bool, Field(description="true=等待完成，false=提交后返回task_id", default=True)
    ] = True,
    confirm: Annotated[
        bool, Field(description="必须为 true 才会实际调用付费生成接口", default=False)
    ] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """文生视频。standard 档 wan2.7-t2v，pro 档 1080P，max 档 happyhorse-1.1-t2v。"""
    tier = tier if tier in {"standard", "pro", "max"} else _default_tier()
    if duration < 1 or duration > max_video_duration():
        raise ReadImageError(
            tr(
                f"视频时长 {duration}s 超出上限 {max_video_duration()}s"
                "（OMNIMODAL_MAX_VIDEO_DURATION）。",
                f"Duration {duration}s exceeds limit {max_video_duration()}s.",
            )
        )
    spec = _t2v_spec(tier)
    cost = f"预计 {spec.price_hint * duration:.2f} 元（{duration} 秒 × {spec.price_hint} 元/秒）"
    if not confirm:
        return {
            "status": "NEEDS_CONFIRMATION",
            "cost": cost,
            "note": tr(
                "设置 confirm=true 后才会实际调用付费生成接口。",
                "Set confirm=true to call the paid generation API.",
            ),
        }
    payload = {
        "model": spec.model,
        "input": {"prompt": prompt},
        "parameters": {
            "duration": duration,
            "resolution": _video_resolution(resolution),
            "ratio": "16:9",
            "prompt_extend": True,
            "watermark": False,
        },
    }
    return await asyncio.to_thread(_submit_video_task, spec, payload, wait, ctx, cost)


@mcp.tool(name="omnimodal_generate_video_from_image")
async def omnimodal_generate_video_from_image(
    image: Annotated[str, Field(description="首帧图片路径或URL")],
    prompt: Annotated[str, Field(description="视频内容描述")],
    tier: Annotated[
        str,
        Field(
            description="档位: standard(wan2.7-i2v)/pro(happyhorse-1.1-i2v)",
            default="standard",
        ),
    ] = "standard",
    duration: Annotated[int, Field(description="秒数", default=5)] = 5,
    resolution: Annotated[
        str,
        Field(description="720P/1080P；480P 会自动升级为 720P", default="720P"),
    ] = "720P",
    wait: Annotated[
        bool, Field(description="true=等待完成，false=返回task_id", default=True)
    ] = True,
    confirm: Annotated[
        bool, Field(description="必须为 true 才会实际调用付费生成接口", default=False)
    ] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """图生视频：以图片为首帧生成视频。"""
    tier = tier if tier in {"standard", "pro", "max"} else _default_tier()
    spec = GenerationSpec(
        endpoint=T2V_ENDPOINT,
        model=video_generation_model(tier, "i2v"),
        poll_interval=15,
        timeout_sec=video_generation_timeout_sec(),
        price_hint={"standard": 0.60, "pro": 0.90, "max": 1.20}.get(tier, 0.60),
        price_unit=tr("秒", "sec"),
    )
    cost = f"预计 {spec.price_hint * duration:.2f} 元（{duration} 秒 × {spec.price_hint} 元/秒）"
    if not confirm:
        return {
            "status": "NEEDS_CONFIRMATION",
            "cost": cost,
            "note": tr(
                "设置 confirm=true 后才会实际调用付费生成接口。",
                "Set confirm=true to call the paid generation API.",
            ),
        }
    media_url = _media_source_for_generation(image, spec.model, "image/png")
    payload = {
        "model": spec.model,
        "input": {
            "prompt": prompt,
            "media": [{"type": "first_frame", "url": media_url}],
        },
        "parameters": {
            "duration": duration,
            "resolution": _video_resolution(resolution),
            "prompt_extend": True,
            "watermark": False,
        },
    }
    return await asyncio.to_thread(_submit_video_task, spec, payload, wait, ctx, cost)


@mcp.tool(name="omnimodal_edit_video")
async def omnimodal_edit_video(
    video: Annotated[str, Field(description="输入视频路径或URL")],
    prompt: Annotated[str, Field(description="视频编辑指令")],
    tier: Annotated[
        str,
        Field(
            description="档位: standard/pro/max，当前均使用 wan2.7-videoedit",
            default="standard",
        ),
    ] = "standard",
    duration: Annotated[int, Field(description="输出秒数", default=5)] = 5,
    resolution: Annotated[str, Field(description="如 480P/720P/1080P", default="720P")] = "720P",
    reference_image: Annotated[
        str | None,
        Field(
            description="可选参考图片路径或URL",
            default=None,
        ),
    ] = None,
    wait: Annotated[
        bool, Field(description="true=等待完成，false=返回task_id", default=True)
    ] = True,
    confirm: Annotated[
        bool, Field(description="必须为 true 才会实际调用付费生成接口", default=False)
    ] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """视频编辑：按指令修改已有视频，支持参考图。"""
    tier = tier if tier in {"standard", "pro", "max"} else _default_tier()
    spec = GenerationSpec(
        endpoint=T2V_ENDPOINT,
        model=video_generation_model(tier, "edit"),
        poll_interval=15,
        timeout_sec=video_generation_timeout_sec(),
        price_hint=0.60,
        price_unit=tr("秒", "sec"),
    )
    cost = f"预计 {spec.price_hint * duration:.2f} 元（{duration} 秒 × {spec.price_hint} 元/秒）"
    if not confirm:
        return {
            "status": "NEEDS_CONFIRMATION",
            "cost": cost,
            "note": tr(
                "设置 confirm=true 后才会实际调用付费生成接口。",
                "Set confirm=true to call the paid generation API.",
            ),
        }
    media: list[dict[str, Any]] = [
        {
            "type": "video",
            "url": _media_source_for_generation(video, spec.model, "video/mp4"),
        }
    ]
    if reference_image:
        media.append(
            {
                "type": "reference_image",
                "url": _media_source_for_generation(
                    reference_image,
                    spec.model,
                    "image/png",
                ),
            }
        )
    payload = {
        "model": spec.model,
        "input": {
            "prompt": prompt,
            "media": media,
        },
        "parameters": {
            "duration": duration,
            "resolution": _video_resolution(resolution),
            "prompt_extend": True,
            "watermark": False,
        },
    }
    return await asyncio.to_thread(_submit_video_task, spec, payload, wait, ctx, cost)


@mcp.tool(name="omnimodal_generate_audio")
async def omnimodal_generate_audio(
    text: Annotated[str, Field(description="要合成的文本、声音描述或音乐描述")],
    voice: Annotated[
        str, Field(description="音色或参考音频；auto 按模型选择默认系统音色", default="auto")
    ] = "auto",
    tier: Annotated[
        str, Field(description=("档位: standard/pro/max，音频生成专用档"), default="standard")
    ] = "standard",
    kind: Annotated[
        str,
        Field(
            description="tts=语音合成，clone=声音克隆，voice_design=声音设计，music=音乐生成",
            default="tts",
        ),
    ] = "tts",
    preview_text: Annotated[
        str | None,
        Field(
            description="声音设计/克隆时的预听文本；不填时使用默认短句",
            default=None,
        ),
    ] = None,
    wait: Annotated[
        bool, Field(description="true=等待完成，false=返回task_id", default=True)
    ] = True,
    confirm: Annotated[
        bool, Field(description="必须为 true 才会实际调用付费生成接口", default=False)
    ] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """生成语音、克隆声音、声音设计或音乐。必须 confirm=true 才会调用付费接口。"""
    tier = tier if tier in {"standard", "pro", "max"} else _default_tier()
    spec = _tts_spec(tier)
    chars = len(text) * 2  # 汉字按2字符计费
    if kind == "music":
        cost = f"预计 10 秒约 {0.002 * 10:.3f} 元"
    elif kind in {"clone", "voice_design"}:
        cost = "预计 0.2 元/次"
    else:
        cost = f"预计 {chars / 10000 * spec.price_hint:.3f} 元（约 {chars} 字符）"
    if not confirm:
        return {
            "status": "NEEDS_CONFIRMATION",
            "cost": cost,
            "note": tr(
                "设置 confirm=true 后才会实际调用付费生成接口。",
                "Set confirm=true to call the paid generation API.",
            ),
        }
    if kind == "music":
        model = "fun-music-v1"
        endpoint = MUSIC_ENDPOINT
        payload = _music_payload(text)
        saved = _submit_audio_sync(endpoint, payload, model)
        return {"status": "SUCCEEDED", "files": [str(saved)], "cost": cost}
    elif kind == "voice_design":
        model = QWEN_TTS_VD_MODEL
        voice_id = _create_custom_voice(
            text,
            model,
            model="qwen-voice-design",
        )
        speech_text = preview_text or "各位听众朋友大家好，欢迎收听本期节目。"
        endpoint, payload = _tts_request(model, speech_text, voice_id)
        saved = _submit_audio_sync(endpoint, payload, model)
        return {"status": "SUCCEEDED", "files": [str(saved)], "cost": cost}
    elif kind == "clone":
        if not voice or voice == "auto":
            raise ReadImageError(
                tr(
                    "声音克隆必须提供参考音频路径或URL（voice 参数）。",
                    "Voice cloning requires a reference audio path or URL in voice.",
                )
            )
        model = QWEN_TTS_VC_MODEL
        voice_id = _create_custom_voice(
            "",
            model,
            audio=voice,
            model="qwen-voice-enrollment",
        )
        endpoint, payload = _tts_request(model, text, voice_id)
        saved = _submit_audio_sync(endpoint, payload, model)
        return {"status": "SUCCEEDED", "files": [str(saved)], "cost": cost}
    else:
        model = spec.model
        endpoint, payload = _tts_request(model, text, voice)
    saved = _submit_audio_sync(endpoint, payload, model)
    return {"status": "SUCCEEDED", "files": [str(saved)], "cost": cost}


@mcp.tool(name="omnimodal_transcribe_audio")
async def omnimodal_transcribe_audio(
    audio: Annotated[str, Field(description="音频文件路径或URL")],
    language: Annotated[str, Field(description="语言提示，默认 zh", default="zh")] = "zh",
    wait: Annotated[
        bool, Field(description="true=等待完成，false=返回task_id", default=True)
    ] = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """语音转文字（fun-asr/paraformer/qwen3-asr 等 ASR 模型）。"""
    result = audio_processing.transcribe_audio(
        audio,
        language=language,
        wait=wait,
        progress_cb=_report(ctx),
    )
    return result


@mcp.tool(name="omnimodal_get_task_result")
async def omnimodal_get_task_result(
    task_id: Annotated[str, Field(description="生成/转写任务的 task_id")],
) -> dict[str, Any]:
    """查询之前提交的异步生成/转写任务结果。

    SUCCEEDED 时返回结果 URL（图片/视频）或转写文本；
    FAILED/CANCELED 时返回错误信息；处理中返回当前状态。
    """
    client = GenerationClient(GenerationSpec(endpoint=T2I_ENDPOINT, model=""))
    data = client.poll_status(task_id)
    output = data.get("output", {}) if isinstance(data, dict) else {}
    status = output.get("task_status")
    if status == "SUCCEEDED":
        results = output.get("results")
        result_url: Any = None
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                result_url = first.get("url") or first.get("video_url")
        if result_url is None:
            result_url = output.get("video_url")
        return {"task_id": task_id, "status": status, "result_url": result_url}
    if status in {"FAILED", "CANCELED"}:
        message = output.get("message") or output.get("error") or ""
        return {"task_id": task_id, "status": status, "error": str(message)}
    return {"task_id": task_id, "status": status}


def _report(ctx: Context | None):
    def cb(progress: int, total: int | None, message: str | None) -> None:
        if ctx is not None:
            try:
                # FastMCP Context.report_progress is a coroutine; in the sync
                # callback we fire it on the running loop if any, else ignore.
                import asyncio

                coro = ctx.report_progress(progress, total, message)
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.create_task(coro)
            except Exception:
                pass

    return cb


def _download(client: GenerationClient, url: str) -> Path:
    import hashlib
    import re

    stem = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1].split("?")[0] or "result")
    ext = Path(stem).suffix or ".bin"
    name = f"{int(time.time())}-{hashlib.md5(url.encode()).hexdigest()[:8]}{ext}"
    return client.download_result(url, name)


# Internal CLI/test compatibility aliases; MCP tool names remain omnimodal_*.
generate_image = omnimodal_generate_image
generate_video = omnimodal_generate_video
generate_video_from_image = omnimodal_generate_video_from_image
generate_video_edit = omnimodal_edit_video
edit_video = omnimodal_edit_video
generate_speech = omnimodal_generate_audio
transcribe_audio = omnimodal_transcribe_audio
get_generation_result = omnimodal_get_task_result


def main() -> None:
    if len(sys.argv) > 1:
        raise SystemExit(
            tr(
                "generation server 仅支持 stdio 模式。",
                "generation server only supports stdio mode.",
            )
        )
    mcp.run()


if __name__ == "__main__":
    main()
