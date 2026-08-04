"""MCP server for generation capabilities (image/video/speech + ASR).

Separate process from the vision server so long-running generation tasks
(1-5 min video) never block recognition. Exposes:

- generate_image(prompt, ...)
- generate_video(prompt, ...)
- generate_video_from_image(image, ...)
- generate_speech(text, ...)
- transcribe_audio(audio, ...)
- get_generation_result(task_id)

Tier selection (standard/pro/max) maps to model gradients; default tier is
configurable via READ_IMAGE_DEFAULT_TIER. Cost is always surfaced to the
caller before and after a task.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from omnimodal import audio_processing
from omnimodal.config import api_key
from omnimodal.errors import ReadImageError, tr
from omnimodal.generation import (
    GenerationClient,
    GenerationSpec,
    GenerationTimeoutError,
    generation_timeout_sec,
    max_video_duration,
    video_generation_timeout_sec,
)
from omnimodal.http import http_client
from omnimodal.paths import ensure_allowed_output_dir

mcp = FastMCP("generation")

# ---- shared helpers -------------------------------------------------------

_DASH_REST = "https://dashscope.aliyuncs.com/api/v1/services"
T2I_ENDPOINT = f"{_DASH_REST}/aigc/text2image/image-synthesis"
T2V_ENDPOINT = f"{_DASH_REST}/aigc/video-generation/video-synthesis"
TTS_ENDPOINT = f"{_DASH_REST}/audio/tts/SpeechSynthesizer"
ASR_ENDPOINT = audio_processing.ASR_ENDPOINT


def _default_tier() -> str:
    value = os.environ.get("READ_IMAGE_DEFAULT_TIER", "standard").strip().lower()
    return value if value in {"standard", "pro", "max"} else "standard"


def _output_dir() -> Path:
    """Resolve and validate the generation output directory."""
    configured = os.environ.get("READ_IMAGE_GENERATION_OUTPUT_DIR", "").strip()
    allowed = ensure_allowed_output_dir(configured or None)  # raises if not allowed
    return Path(allowed) if allowed else Path.cwd()


def _format_cost(spec: GenerationSpec, detail: str) -> str:
    return f"{detail}（费用{spec.cost_label}）" if spec.price_hint else detail


def _t2i_spec(tier: str) -> GenerationSpec:
    model = "wanx2.1-t2i-plus" if tier == "pro" else "wanx2.1-t2i-turbo"
    return GenerationSpec(
        endpoint=T2I_ENDPOINT,
        model=model,
        poll_interval=10,
        timeout_sec=generation_timeout_sec(),
        price_hint=0.20 if tier == "pro" else 0.14,
        price_unit=tr("张", "image"),
    )


def _t2v_spec(tier: str) -> GenerationSpec:
    if tier == "max":
        model, price, unit = "happyhorse-1.1-t2v", 1.20, tr("秒", "sec")
    elif tier == "pro":
        model, price, unit = "wan2.6-t2v", 0.45, tr("秒", "sec")
    else:
        model, price, unit = "wanx2.1-t2v-turbo", 0.24, tr("秒", "sec")
    return GenerationSpec(
        endpoint=T2V_ENDPOINT,
        model=model,
        poll_interval=15,
        timeout_sec=video_generation_timeout_sec(),
        price_hint=price,
        price_unit=unit,
    )


def _tts_spec(tier: str) -> GenerationSpec:
    model = "cosyvoice-v3" if tier == "pro" else "cosyvoice-v2"
    return GenerationSpec(
        endpoint=TTS_ENDPOINT,
        model=model,
        poll_interval=10,
        timeout_sec=generation_timeout_sec(),
        price_hint=0.20,  # per 1000 chars
        price_unit=tr("千字符", "k chars"),
    )


# ---- tools ----------------------------------------------------------------

@mcp.tool()
async def generate_image(
    prompt: Annotated[str, Field(description="图像描述，≤500 字")],
    tier: Annotated[str, Field(
        description="档位: standard(0.14元)/pro(0.2元)",
        default="standard"
        )] = "standard",
    size: Annotated[str, Field(description="尺寸如 1024*1024", default="1024*1024")] = "1024*1024",
    n: Annotated[int, Field(description="生成数量 1-4", default=1)] = 1,
    wait: Annotated[
        bool, Field(description="true=等待完成返回结果，false=提交后返回task_id", default=True)
    ] = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """文生图。standard 档 0.14 元/张，pro 档 0.2 元/张。"""
    tier = tier if tier in {"standard", "pro"} else _default_tier()
    if n < 1 or n > 4:
        raise ReadImageError(tr("生成数量 n 必须在 1-4 之间。", "n must be 1-4."))
    spec = _t2i_spec(tier)
    cost = f"预计 {spec.price_hint * n:.2f} 元（{n} 张 × {spec.price_hint} 元/张）"
    payload = {
        "model": spec.model,
        "input": {"prompt": prompt},
        "parameters": {"size": size, "n": n, "prompt_extend": True, "watermark": False},
    }
    client = GenerationClient(spec, output_dir=str(_output_dir()))
    task_id = client.submit(payload)
    if not wait:
        return {"task_id": task_id, "status": "PENDING", "cost": cost}
    try:
        data = client.wait_for_result(task_id, progress_cb=_report(ctx))
    except GenerationTimeoutError as exc:
        return {"task_id": exc.task_id, "status": "RUNNING", "cost": cost, "note": str(exc)}
    results = data.get("output", {}).get("results", [])
    urls = [r.get("url") for r in results if isinstance(r, dict) and r.get("url")]
    saved = [_download(client, url) for url in urls if isinstance(url, str)]
    return {"status": "SUCCEEDED", "files": [str(p) for p in saved], "cost": cost}


@mcp.tool()
async def generate_video(
    prompt: Annotated[str, Field(description="视频内容描述")],
    tier: Annotated[str, Field(
        description="档位: standard(0.24元/秒)/pro(wan2.6 0.45元/秒)/max(happyhorse 1.2元/秒)",
        default="standard"
        )] = "standard",
    duration: Annotated[int, Field(description="秒数", default=5)] = 5,
    resolution: Annotated[str, Field(description="如 480P/720P/1080P", default="480P")] = "480P",
    wait: Annotated[
        bool, Field(description="true=等待完成，false=提交后返回task_id", default=True)
    ] = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """文生视频。standard 档 0.24 元/秒，5 秒约 1.2 元。"""
    tier = tier if tier in {"standard", "pro", "max"} else _default_tier()
    if duration < 1 or duration > max_video_duration():
        raise ReadImageError(
            tr(
                f"视频时长 {duration}s 超出上限 {max_video_duration()}s"
                "（READ_IMAGE_MAX_VIDEO_DURATION）。",
                f"Duration {duration}s exceeds limit {max_video_duration()}s.",
            )
        )
    spec = _t2v_spec(tier)
    cost = f"预计 {spec.price_hint * duration:.2f} 元（{duration} 秒 × {spec.price_hint} 元/秒）"
    payload = {
        "model": spec.model,
        "input": {"prompt": prompt},
        "parameters": {
            "duration": duration,
            "resolution": resolution,
            "prompt_extend": True,
            "watermark": False,
        },
    }
    client = GenerationClient(spec, output_dir=str(_output_dir()))
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


@mcp.tool()
async def generate_video_from_image(
    image: Annotated[str, Field(description="首帧图片路径或URL")],
    prompt: Annotated[str, Field(description="视频内容描述")],
    tier: Annotated[str, Field(description="档位", default="standard")] = "standard",
    wait: Annotated[
        bool, Field(description="true=等待完成，false=返回task_id", default=True)
    ] = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """图生视频：以图片为首帧生成视频。"""
    tier = tier if tier in {"standard", "pro"} else _default_tier()
    spec = GenerationSpec(
        endpoint=T2V_ENDPOINT,
        model="wanx2.1-i2v-turbo" if tier == "standard" else "wan2.6-i2v-flash",
        poll_interval=15,
        timeout_sec=video_generation_timeout_sec(),
        price_hint=0.24,
        price_unit=tr("秒", "sec"),
    )
    cost = f"预计 5 秒约 {0.24 * 5:.2f} 元"
    payload = {
        "model": spec.model,
        "input": {"prompt": prompt, "img_url": image},
        "parameters": {"duration": 5, "resolution": "480P", "prompt_extend": True},
    }
    client = GenerationClient(spec, output_dir=str(_output_dir()))
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


@mcp.tool()
async def generate_speech(
    text: Annotated[str, Field(description="要合成的文本")],
    voice: Annotated[str, Field(
        description="音色，默认 longxiaochun_v2",
        default="longxiaochun_v2"
        )] = "longxiaochun_v2",
    tier: Annotated[str, Field(
        description="档位: standard(cosyvoice-v2)/pro(v3)",
        default="standard"
        )] = "standard",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """语音合成 TTS。2 元/万字符（约 0.4 元/千汉字）。"""
    tier = tier if tier in {"standard", "pro"} else _default_tier()
    spec = _tts_spec(tier)
    chars = len(text) * 2  # 汉字按2字符计费
    cost = f"预计 {chars / 1000 * 0.2:.3f} 元（约 {chars} 字符）"
    payload = {
        "model": spec.model,
        "input": {"text": text, "voice": voice, "format": "mp3", "sample_rate": 24000},
    }
    client = GenerationClient(spec, output_dir=str(_output_dir()))
    try:
        response = http_client.post(
            TTS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60.0,
        )
    except Exception as exc:
        raise ReadImageError(tr("TTS 请求失败。", "TTS request failed.")) from exc
    if response.status_code >= 400:
        raise ReadImageError(
            tr(
                f"TTS 请求失败（HTTP {response.status_code}）。",
                f"TTS failed (HTTP {response.status_code}).",
            )
        )
    try:
        parsed = response.json()
        audio_url = parsed.get("output", {}).get("audio", {}).get("url")
    except (json.JSONDecodeError, AttributeError, KeyError):
        audio_url = None
    if not audio_url:
        raise ReadImageError(tr("TTS 响应缺少音频 URL。", "TTS response missing audio URL."))
    saved = _download(client, audio_url)
    return {"status": "SUCCEEDED", "files": [str(saved)], "cost": cost}


@mcp.tool()
async def transcribe_audio(
    audio: Annotated[str, Field(description="音频文件路径或URL")],
    language: Annotated[str, Field(description="语言提示，默认 zh", default="zh")] = "zh",
    wait: Annotated[
        bool, Field(description="true=等待完成，false=返回task_id", default=True)
    ] = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """语音转文字（paraformer-v2，0.288 元/小时，每月 10 小时免费）。"""
    result = audio_processing.transcribe_audio(
        audio,
        language=language,
        wait=wait,
        progress_cb=_report(ctx),
    )
    return result


@mcp.tool()
async def get_generation_result(
    task_id: Annotated[str, Field(description="生成/转写任务的 task_id")],
) -> dict[str, Any]:
    """查询之前提交的异步生成/转写任务结果。"""
    return {
        "task_id": task_id,
        "note": tr(
            "结果下载请使用对应工具重新等待或查询。",
            "Query via the matching tool.",
        ),
    }


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
