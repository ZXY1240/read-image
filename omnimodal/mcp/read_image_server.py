from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from omnimodal import api, audio_processing
from omnimodal.cache import ImageCache, image_cache_key
from omnimodal.config import (
    DEFAULT_AUDIO_TASK,
    DEFAULT_BATCH_TASK,
    DEFAULT_BATCH_WORKERS,
    DEFAULT_MODE,
    DEFAULT_TASK,
    DEFAULT_VIDEO_TASK,
    MAX_BATCH_WORKERS,
    cache_max_entries,
    cache_ttl_sec,
    cache_use_task,
    env_int,
)
from omnimodal.drag import resolve_dragged_path, scan_dragged_media
from omnimodal.errors import ReadImageError, tr
from omnimodal.logging import configure_logging
from omnimodal.mcp.common import EXTERNAL_SEND_ANNOTATIONS, run_cli
from omnimodal.media import analyze_video, prepare_image_variants
from omnimodal.profiles import profile_for_mode
from omnimodal.workers import run_video_task

mcp = FastMCP("omnimodal-recognize")
logger = configure_logging("omnimodal-recognize")
_image_cache = ImageCache(cache_max_entries(), ttl_sec=cache_ttl_sec())
BATCH_TIMEOUT_BUFFER_SEC = 30
CLIPBOARD_SAVE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "save_clipboard_image.ps1"


def _is_windows() -> bool:
    return os.name == "nt"


def _format_slice_results(results: list[str]) -> str:
    if len(results) == 1:
        return results[0]
    return "\n\n".join(
        f"## 第 {index + 1}/{len(results)} 段\n\n{result}" for index, result in enumerate(results)
    )


def _report_progress_sync(
    ctx: Context,
    loop: asyncio.AbstractEventLoop,
    progress: int,
    total: int,
    message: str,
) -> None:
    """从 worker 线程同步提交一次进度通知并等待发送完成。

    `ctx.report_progress` 是协程，只能在事件循环中 await；当同步实现在线程中
    运行时（如批量识别），通过 run_coroutine_threadsafe 把通知投递回主循环。
    进度通知是尽力而为的，发送失败不应影响识别结果。
    """
    try:
        future = asyncio.run_coroutine_threadsafe(
            ctx.report_progress(progress, total, message),
            loop,
        )
        future.result()
    except Exception as exc:
        logger.debug("failed to send progress notification: %s", exc)


def _run_image_with_cache(
    image_bytes: bytes,
    mime_type: str,
    task: str,
    mode: str,
    timeout_sec: int | None = None,
) -> str:
    profile = profile_for_mode(mode)
    provider = api.default_client.provider
    key = image_cache_key(
        image_bytes,
        profile.key,
        provider.model,
        provider.provider_name,
        task=task,
        use_task=cache_use_task(),
    )
    cached = _image_cache.get(key)
    if cached is not None:
        logger.info("image cache hit for %s", profile.key)
        return cached
    result = api.call_image(
        image_bytes,
        task,
        mode,
        mime_type=mime_type,
        timeout_sec=timeout_sec,
    )
    _image_cache.put(key, result)
    return result


def read_image(
    image: str,
    task: str = DEFAULT_TASK,
    mode: str = DEFAULT_MODE,
) -> str:
    """读取本地图片、data URL 或 base64 图片（同步实现，供 CLI 与内部调用）。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_TASK
    profile_for_mode(mode)
    variants = prepare_image_variants(image)
    results = [
        _run_image_with_cache(image_bytes, mime_type, effective_task, mode)
        for image_bytes, mime_type in variants
    ]
    return _format_slice_results(results)


@mcp.tool(name="omnimodal_recognize_image", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_recognize_image_tool(
    image: Annotated[
        str,
        Field(description="本地图片路径、data URL 或 base64 图片数据。"),
    ],
    task: Annotated[
        str,
        Field(description="本次要从图片中提取的具体内容，未传时默认详细描述图片内容。"),
    ] = DEFAULT_TASK,
    mode: Annotated[
        str,
        Field(
            description=(
                "识别档位：quick/standard/full/quick_analysis/balanced_analysis/"
                "deep_analysis，默认 standard。"
            )
        ),
    ] = DEFAULT_MODE,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """识别本地图片、data URL 或 base64 图片，并按 task 和 mode 调用视觉模型。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_TASK
    profile_for_mode(mode)
    variants = prepare_image_variants(image)
    total = max(1, len(variants))
    results: list[str] = []
    for index, (image_bytes, mime_type) in enumerate(variants, start=1):
        await ctx.report_progress(
            index - 1,
            total,
            tr(
                f"正在识别第 {index}/{total} 段…",
                f"Analyzing slice {index}/{total}...",
            ),
        )
        results.append(_run_image_with_cache(image_bytes, mime_type, effective_task, mode))
    await ctx.report_progress(total, total, tr("图片识别完成。", "Image recognition complete."))
    return _format_slice_results(results)


def read_clipboard_image(
    task: str = DEFAULT_TASK,
    mode: str = DEFAULT_MODE,
) -> str:
    """保存并读取 Windows 剪贴板图片（同步实现，供 CLI 与内部调用）。"""
    if not _is_windows():
        raise ReadImageError(
            tr(
                "read_clipboard_image 仅支持 Windows。",
                "read_clipboard_image is only supported on Windows.",
            )
        )
    effective_task = task.strip() if task and task.strip() else DEFAULT_TASK
    profile_for_mode(mode)
    if not CLIPBOARD_SAVE_SCRIPT.is_file():
        raise ReadImageError(
            tr(
                f"找不到剪贴板保存脚本：{CLIPBOARD_SAVE_SCRIPT}",
                f"Clipboard save script not found: {CLIPBOARD_SAVE_SCRIPT}",
            )
        )
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(CLIPBOARD_SAVE_SCRIPT),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReadImageError(
            tr(
                "剪贴板读取超时。",
                "Clipboard image read timed out.",
            )
        ) from exc
    except OSError as exc:
        raise ReadImageError(
            tr(
                f"无法启动剪贴板保存脚本：{exc}",
                f"Could not start clipboard save script: {exc}",
            )
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if "clipboard does not contain an image" in detail.lower():
            raise ReadImageError(
                tr(
                    "剪贴板中没有图片。请先把图片保存成文件后再调用 read_image。",
                    "Clipboard does not contain an image. Save the image to a file "
                    "and call read_image instead.",
                )
            )
        raise ReadImageError(
            tr(
                f"剪贴板图片保存失败：{detail}",
                f"Clipboard image save failed: {detail}",
            )
        )

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise ReadImageError(
            tr(
                "剪贴板图片保存失败，未返回文件路径。",
                "Clipboard image save failed without returning a path.",
            )
        )
    saved_path = Path(lines[-1])
    if not saved_path.is_file():
        raise ReadImageError(
            tr(
                f"剪贴板图片保存失败，文件不存在：{saved_path}",
                f"Clipboard image save failed; file not found: {saved_path}",
            )
        )
    return read_image(str(saved_path), effective_task, mode)


@mcp.tool(name="omnimodal_read_clipboard_image", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_read_clipboard_image_tool(
    task: Annotated[
        str,
        Field(description="本次要从剪贴板图片中提取或分析的具体内容。"),
    ] = DEFAULT_TASK,
    mode: Annotated[
        str,
        Field(
            description=(
                "识别档位：quick/standard/full/quick_analysis/balanced_analysis/"
                "deep_analysis，默认 standard。"
            )
        ),
    ] = DEFAULT_MODE,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """保存并读取 Windows 剪贴板图片，直接返回识别结果。"""
    await ctx.report_progress(0, 1, tr("正在读取剪贴板图片…", "Reading clipboard image..."))
    result = read_clipboard_image(task, mode)
    await ctx.report_progress(
        1,
        1,
        tr("剪贴板图片识别完成。", "Clipboard image recognition complete."),
    )
    return result


def _format_drag_candidates(kind: str, candidates: list[Path]) -> str:
    lines = [
        f"找到多个拖拽{kind}候选，请指定 path 参数后重新调用：",
        "",
    ]
    for index, path in enumerate(candidates, start=1):
        lines.append(f"{index}. {path}")
    return "\n".join(lines)


def read_dragged_image(
    task: str = DEFAULT_TASK,
    mode: str = DEFAULT_MODE,
    path: str | None = None,
) -> str:
    """扫描最近拖入的图片（同步实现，供 CLI 与内部调用）。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_TASK
    profile_for_mode(mode)
    if path:
        selected = resolve_dragged_path(path, "image")
        return f"已识别拖拽图片：{selected}\n\n{read_image(str(selected), effective_task, mode)}"
    candidates = scan_dragged_media("image")
    if not candidates:
        raise ReadImageError(
            tr(
                "Claude 桌面端拖入的图片不落盘，无法扫描。请复制进剪贴板后调用 "
                "read_clipboard_image，或保存为文件后提供路径调用 read_image。",
                "Dragged images in Claude Desktop are not written to disk and cannot be "
                "scanned. Copy the image to the clipboard and call read_clipboard_image, "
                "or save it to a file and call read_image with the path.",
            )
        )
    if len(candidates) == 1:
        selected = candidates[0]
        return f"已识别拖拽图片：{selected}\n\n{read_image(str(selected), effective_task, mode)}"
    return _format_drag_candidates("图片", candidates)


@mcp.tool(name="omnimodal_read_dragged_image", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_read_dragged_image_tool(
    task: Annotated[
        str,
        Field(description="本次要从拖拽图片中提取或分析的具体内容。"),
    ] = DEFAULT_TASK,
    mode: Annotated[
        str,
        Field(
            description=(
                "识别档位：quick/standard/full/quick_analysis/balanced_analysis/"
                "deep_analysis，默认 standard。"
            )
        ),
    ] = DEFAULT_MODE,
    path: Annotated[
        str | None,
        Field(description="多候选时指定要识别的拖拽图片路径。"),
    ] = None,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """扫描最近拖入的图片，单候选自动识别，多候选要求指定 path。

    仅适用于会将拖拽文件落盘的客户端环境；Claude 桌面端拖入的图片不落盘，
    无法扫描（请改用 read_clipboard_image 或 read_image）。
    """
    await ctx.report_progress(0, 1, tr("正在扫描拖拽图片…", "Scanning dragged images..."))
    result = read_dragged_image(task, mode, path=path)
    await ctx.report_progress(1, 1, tr("拖拽图片识别完成。", "Dragged image recognition complete."))
    return result


def read_dragged_video(
    task: str = DEFAULT_VIDEO_TASK,
    mode: str = DEFAULT_MODE,
    path: str | None = None,
) -> str:
    """扫描最近拖入的视频（同步实现，供 CLI 与内部调用）。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_VIDEO_TASK
    profile_for_mode(mode)
    if path:
        selected = resolve_dragged_path(path, "video")
        return f"已识别拖拽视频：{selected}\n\n{read_video(str(selected), effective_task, mode)}"
    candidates = scan_dragged_media("video")
    if not candidates:
        raise ReadImageError(
            tr(
                "Claude 桌面端拖入的视频不落盘，无法扫描。视频无法复制到剪贴板，"
                "请把视频保存到明确路径后调用 read_video。",
                "Dragged videos in Claude Desktop are not written to disk and cannot be "
                "scanned. Videos cannot be copied to the clipboard; save the video to "
                "a file and call read_video with the path.",
            )
        )
    if len(candidates) == 1:
        selected = candidates[0]
        return f"已识别拖拽视频：{selected}\n\n{read_video(str(selected), effective_task, mode)}"
    return _format_drag_candidates("视频", candidates)


@mcp.tool(name="omnimodal_read_dragged_video", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_read_dragged_video_tool(
    task: Annotated[
        str,
        Field(description="本次要从拖拽视频中提取或分析的具体内容。"),
    ] = DEFAULT_VIDEO_TASK,
    mode: Annotated[
        str,
        Field(
            description=(
                "识别档位：quick/standard/full/quick_analysis/balanced_analysis/"
                "deep_analysis，默认 standard。"
            )
        ),
    ] = DEFAULT_MODE,
    path: Annotated[
        str | None,
        Field(description="多候选时指定要识别的拖拽视频路径。"),
    ] = None,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """扫描最近拖入的视频，单候选自动识别，多候选要求指定 path。

    仅适用于会将拖拽文件落盘的客户端环境；Claude 桌面端拖入的视频不落盘，
    无法扫描（请改用 read_clipboard_image 或 read_video）。
    """
    await ctx.report_progress(0, 1, tr("正在扫描拖拽视频…", "Scanning dragged videos..."))
    result = read_dragged_video(task, mode, path=path)
    await ctx.report_progress(1, 1, tr("拖拽视频识别完成。", "Dragged video recognition complete."))
    return result


def read_video(
    video: str,
    task: str = DEFAULT_VIDEO_TASK,
    mode: str = DEFAULT_MODE,
) -> str:
    """读取本地视频或视频 URL（同步实现，供 CLI 与内部调用）。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_VIDEO_TASK
    profile_for_mode(mode)
    return run_video_task(analyze_video, video, effective_task, mode)


def _format_audio_result(result: str | dict[str, object]) -> str:
    if isinstance(result, str):
        return result
    text = result.get("text")
    if isinstance(text, str) and text:
        return text
    return json.dumps(result, ensure_ascii=False)


def read_audio(
    audio: str,
    task: str = DEFAULT_AUDIO_TASK,
    mode: str = DEFAULT_MODE,
    tier: str = "standard",
) -> str:
    """读取本地音频或音频 URL（同步实现，供 CLI 与内部调用）。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_AUDIO_TASK
    profile_for_mode(mode)
    return _format_audio_result(
        audio_processing.recognize_audio(
            audio,
            task=effective_task,
            mode=mode,
            tier=tier,
        )
    )


@mcp.tool(name="omnimodal_recognize_audio", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_recognize_audio_tool(
    audio: Annotated[
        str,
        Field(description="本地音频文件绝对路径，或 http(s) 音频 URL。"),
    ],
    task: Annotated[
        str,
        Field(description="本次要从音频中提取或分析的具体内容。"),
    ] = DEFAULT_AUDIO_TASK,
    mode: Annotated[
        str,
        Field(
            description=(
                "识别档位：quick/standard/full/quick_analysis/balanced_analysis/"
                "deep_analysis，默认 standard。"
            )
        ),
    ] = DEFAULT_MODE,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """识别本地音频或音频 URL；长音频自动走语音转写。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_AUDIO_TASK
    profile_for_mode(mode)
    await ctx.report_progress(0, 100, tr("正在分析音频…", "Analyzing audio..."))
    result = read_audio(audio, effective_task, mode)
    await ctx.report_progress(100, 100, tr("音频识别完成。", "Audio recognition complete."))
    return result


def read_dragged_audio(
    task: str = DEFAULT_AUDIO_TASK,
    mode: str = DEFAULT_MODE,
    path: str | None = None,
) -> str:
    """扫描最近拖入的音频（同步实现，供 CLI 与内部调用）。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_AUDIO_TASK
    profile_for_mode(mode)
    if path:
        selected = resolve_dragged_path(path, "audio")
        return f"已识别拖拽音频：{selected}\n\n{read_audio(str(selected), effective_task, mode)}"
    candidates = scan_dragged_media("audio")
    if not candidates:
        raise ReadImageError(
            tr(
                "没有找到最近拖入的音频。请保存为文件后提供路径调用 omnimodal_recognize_audio。",
                "No recently dragged audio found. Save it to a file and call "
                "omnimodal_recognize_audio with the path.",
            )
        )
    if len(candidates) == 1:
        selected = candidates[0]
        return f"已识别拖拽音频：{selected}\n\n{read_audio(str(selected), effective_task, mode)}"
    return _format_drag_candidates("音频", candidates)


@mcp.tool(name="omnimodal_read_dragged_audio", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_read_dragged_audio_tool(
    task: Annotated[
        str,
        Field(description="本次要从拖拽音频中提取或分析的具体内容。"),
    ] = DEFAULT_AUDIO_TASK,
    mode: Annotated[
        str,
        Field(
            description=(
                "识别档位：quick/standard/full/quick_analysis/balanced_analysis/"
                "deep_analysis，默认 standard。"
            )
        ),
    ] = DEFAULT_MODE,
    path: Annotated[
        str | None,
        Field(description="多候选时指定要识别的拖拽音频路径。"),
    ] = None,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """扫描最近拖入的音频，单候选自动识别，多候选要求指定 path。"""
    await ctx.report_progress(0, 1, tr("正在扫描拖拽音频…", "Scanning dragged audio..."))
    result = read_dragged_audio(task, mode, path=path)
    await ctx.report_progress(1, 1, tr("拖拽音频识别完成。", "Dragged audio recognition complete."))
    return result


@mcp.tool(name="omnimodal_recognize_video", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_recognize_video_tool(
    video: Annotated[
        str,
        Field(description="本地视频文件绝对路径，或 http(s) 视频 URL。"),
    ],
    task: Annotated[
        str,
        Field(description=("本次要从视频中提取或分析的具体内容；未传时默认详细描述视频内容。")),
    ] = DEFAULT_VIDEO_TASK,
    mode: Annotated[
        str,
        Field(
            description=(
                "识别档位：quick/standard/full/quick_analysis/balanced_analysis/"
                "deep_analysis，默认 standard。"
            )
        ),
    ] = DEFAULT_MODE,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """读取本地视频或视频 URL，并按 task 和 mode 调用视频理解模型。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_VIDEO_TASK
    profile_for_mode(mode)
    await ctx.report_progress(0, 100, tr("正在分析视频…", "Analyzing video..."))
    result = run_video_task(analyze_video, video, effective_task, mode)
    await ctx.report_progress(100, 100, tr("视频识别完成。", "Video recognition complete."))
    return result


def _clamp_workers(value: int | None) -> int:
    if value is None:
        requested = env_int("OMNIMODAL_BATCH_WORKERS", DEFAULT_BATCH_WORKERS)
    else:
        try:
            requested = int(value)
        except (TypeError, ValueError):
            requested = DEFAULT_BATCH_WORKERS
    return min(MAX_BATCH_WORKERS, max(1, requested))


def _batch_timeout_sec(mode: str) -> int:
    configured = env_int("OMNIMODAL_BATCH_TIMEOUT_SEC", 0)
    if configured > 0:
        return max(1, configured)
    return profile_for_mode(mode).timeout_sec + BATCH_TIMEOUT_BUFFER_SEC


def _format_batch_results(
    results: list[tuple[int, str, str | None, str | None]],
) -> str:
    sections: list[str] = []
    total = len(results)
    for index, path, content, error in sorted(results, key=lambda item: item[0]):
        filename = path.split("\\")[-1].split("/")[-1]
        if error:
            sections.append(f"## 第 {index + 1}/{total} 张：{filename}\n\n> 错误：{error}")
        else:
            sections.append(f"## 第 {index + 1}/{total} 张：{filename}\n\n{content}")
    return "\n\n".join(sections)


def read_images_batch(
    images: list[str],
    task: str = DEFAULT_BATCH_TASK,
    mode: str = DEFAULT_MODE,
    max_workers: int = DEFAULT_BATCH_WORKERS,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
) -> str:
    """批量读取本地图片（同步实现，供 CLI 与内部调用）。"""
    if not isinstance(images, list) or not images:
        raise ReadImageError("images 参数必须是非空图片路径列表。")
    effective_task = task.strip() if task and task.strip() else DEFAULT_BATCH_TASK
    profile_for_mode(mode)
    workers = _clamp_workers(max_workers)
    timeout_sec = _batch_timeout_sec(mode)
    results: list[tuple[int, str, str | None, str | None] | None] = [None] * len(images)
    result_set = [False] * len(images)
    deadlines = [time.monotonic() + timeout_sec for _ in images]
    stop_event = threading.Event()

    def timeout_result(
        index: int,
        path: str,
    ) -> tuple[int, str, str | None, str | None]:
        return (
            index,
            path,
            None,
            tr(
                f"批量识别超时（超过 {timeout_sec} 秒）。",
                f"Batch image timed out after {timeout_sec}s.",
            ),
        )

    def process(
        index: int,
        path: str,
        deadline: float,
    ) -> tuple[int, str, str | None, str | None]:
        if stop_event.is_set():
            return timeout_result(index, path)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return timeout_result(index, path)
        try:
            variants = prepare_image_variants(path)
            results: list[str] = []
            per_variant_timeout = max(1, int(remaining / max(1, len(variants))))
            for image_bytes, mime_type in variants:
                if stop_event.is_set():
                    return timeout_result(index, path)
                results.append(
                    _run_image_with_cache(
                        image_bytes,
                        mime_type,
                        effective_task,
                        mode,
                        timeout_sec=per_variant_timeout,
                    )
                )
            content = _format_slice_results(results)
            return index, path, content, None
        except ReadImageError as exc:
            return index, path, None, str(exc)
        except Exception as exc:
            return index, path, None, f"未知错误：{exc}"

    executor = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="omnimodal-batch",
    )
    futures = [
        executor.submit(process, index, str(path).strip(), deadlines[index])
        for index, path in enumerate(images)
    ]
    index_by_future = {future: index for index, future in enumerate(futures)}
    try:
        pending = set(futures)
        while pending:
            remaining = min(
                deadlines[index_by_future[future]] - time.monotonic() for future in pending
            )
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=min(remaining, 1.0))
            for future in done:
                index = index_by_future[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = index, str(images[index]).strip(), None, f"未知错误：{exc}"
                if (
                    result is not None
                    and not result_set[index]
                    and time.monotonic() <= deadlines[index]
                ):
                    results[index] = result
                    result_set[index] = True
            if progress_cb is not None:
                done_count = sum(1 for result in results if result is not None)
                progress_cb(done_count, len(images))
        if pending:
            stop_event.set()
            for future in pending:
                index = index_by_future[future]
                if not result_set[index]:
                    results[index] = timeout_result(index, str(images[index]).strip())
                    result_set[index] = True
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    completed = [result for result in results if result is not None]
    if not completed:
        raise ReadImageError("批量识别没有返回任何结果。")
    successful = [result for result in completed if result[2] is not None]
    if not successful:
        errors = "; ".join(str(result[3] or "未知错误") for result in completed[:5])
        raise ReadImageError(f"批量识别全部失败：{errors}")
    return _format_batch_results(completed)


@mcp.tool(name="omnimodal_recognize_images_batch", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_recognize_images_batch_tool(
    images: Annotated[
        list[str],
        Field(
            description=(
                "本地图片路径、data URL 或 base64 图片数据列表，至少 1 项；结果按传入顺序汇总。"
            )
        ),
    ],
    task: Annotated[
        str,
        Field(description="每张图片要提取或分析的统一任务。"),
    ] = DEFAULT_BATCH_TASK,
    mode: Annotated[
        str,
        Field(
            description=(
                "识别档位：quick/standard/full/quick_analysis/balanced_analysis/"
                "deep_analysis，默认 standard。"
            )
        ),
    ] = DEFAULT_MODE,
    max_workers: Annotated[
        int,
        Field(description="并行 worker 数，默认 4，最大 8。"),
    ] = DEFAULT_BATCH_WORKERS,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """批量读取本地图片，使用共享任务队列并行调用视觉模型并按原顺序返回。"""
    if not isinstance(images, list) or not images:
        raise ReadImageError("images 参数必须是非空图片路径列表。")
    total = len(images)
    loop = asyncio.get_running_loop()
    await ctx.report_progress(0, total, tr("开始批量识别…", "Starting batch recognition..."))

    def on_progress(completed: int, _total: int) -> None:
        _report_progress_sync(
            ctx,
            loop,
            completed,
            total,
            tr(
                f"已识别 {completed}/{total} 张…",
                f"Completed {completed}/{total} images...",
            ),
        )

    try:
        return await asyncio.to_thread(
            read_images_batch,
            images,
            task,
            mode,
            max_workers,
            progress_cb=on_progress,
        )
    finally:
        await ctx.report_progress(
            total,
            total,
            tr("批量识别完成。", "Batch recognition complete."),
        )


def _run_media_batch(
    items: list[str],
    task: str,
    mode: str,
    max_workers: int,
    kind: str,
) -> str:
    workers = _clamp_workers(max_workers)
    results: list[str] = []
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"omnimodal-{kind}-batch")
    try:
        futures = []
        for index, item in enumerate(items):

            def call_one(index=index, item=item) -> tuple[int, str, str]:
                try:
                    if kind == "video":
                        return index, item, read_video(item, task, mode)
                    return index, item, read_audio(item, task, mode)
                except Exception as exc:
                    return index, item, f"错误：{exc}"

            futures.append(executor.submit(call_one))
        for future in futures:
            index, item, content = future.result()
            filename = item.split("\\")[-1].split("/")[-1]
            results.append(f"## 第 {index + 1}/{len(items)} 个：{filename}\n\n{content}")
    finally:
        executor.shutdown(wait=True)
    return "\n\n".join(results)


@mcp.tool(name="omnimodal_recognize_videos_batch", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_recognize_videos_batch_tool(
    videos: Annotated[
        list[str],
        Field(description="本地视频路径或 http(s) 视频 URL 列表，至少 1 项。"),
    ],
    task: Annotated[
        str,
        Field(description="每个视频要提取或分析的统一任务。"),
    ] = DEFAULT_VIDEO_TASK,
    mode: Annotated[
        str,
        Field(description="识别档位，默认 standard。"),
    ] = DEFAULT_MODE,
    max_workers: Annotated[
        int,
        Field(description="并行 worker 数，默认 4，最大 8。"),
    ] = DEFAULT_BATCH_WORKERS,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """批量识别视频并按原顺序汇总。"""
    if not isinstance(videos, list) or not videos:
        raise ReadImageError("videos 参数必须是非空列表。")
    effective_task = task.strip() if task and task.strip() else DEFAULT_VIDEO_TASK
    return await asyncio.to_thread(
        _run_media_batch,
        videos,
        effective_task,
        mode,
        max_workers,
        "video",
    )


@mcp.tool(name="omnimodal_recognize_audios_batch", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def _omnimodal_recognize_audios_batch_tool(
    audios: Annotated[
        list[str],
        Field(description="本地音频路径或 http(s) 音频 URL 列表，至少 1 项。"),
    ],
    task: Annotated[
        str,
        Field(description="每个音频要提取或分析的统一任务。"),
    ] = DEFAULT_AUDIO_TASK,
    mode: Annotated[
        str,
        Field(description="识别档位，默认 standard。"),
    ] = DEFAULT_MODE,
    max_workers: Annotated[
        int,
        Field(description="并行 worker 数，默认 4，最大 8。"),
    ] = DEFAULT_BATCH_WORKERS,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """批量识别音频并按原顺序汇总。"""
    if not isinstance(audios, list) or not audios:
        raise ReadImageError("audios 参数必须是非空列表。")
    effective_task = task.strip() if task and task.strip() else DEFAULT_AUDIO_TASK
    return await asyncio.to_thread(
        _run_media_batch,
        audios,
        effective_task,
        mode,
        max_workers,
        "audio",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recognize images, videos, or audio via Qwen/DashScope APIs."
    )
    parser.add_argument(
        "--image",
        action="append",
        help="Local image path, data URL, or base64 image data; repeat for batch mode.",
    )
    parser.add_argument(
        "--video",
        default=None,
        help="Local video path or http(s) video URL.",
    )
    parser.add_argument(
        "--audio",
        default=None,
        help="Local audio path or http(s) audio URL.",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="What to extract or analyze from the input.",
    )
    parser.add_argument("--mode", default=DEFAULT_MODE, help="Vision profile name.")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_BATCH_WORKERS,
        help="Batch worker count (default 4, max 8).",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Read the Windows clipboard image and return vision results.",
    )
    parser.add_argument(
        "--dragged-image",
        action="store_true",
        help="Scan and read a recently dragged image.",
    )
    parser.add_argument(
        "--dragged-video",
        action="store_true",
        help="Scan and read a recently dragged video.",
    )
    parser.add_argument(
        "--dragged-audio",
        action="store_true",
        help="Scan and read a recently dragged audio file.",
    )
    parser.add_argument(
        "--dragged-path",
        default=None,
        help="Explicit dragged media path for --dragged-image/--dragged-video.",
    )
    return parser


def _run_cli_handler(args: argparse.Namespace) -> int:
    if args.dragged_image:
        task = args.task or DEFAULT_TASK
        print(read_dragged_image(task, args.mode, path=args.dragged_path))
    elif args.dragged_audio:
        task = args.task or DEFAULT_AUDIO_TASK
        print(read_dragged_audio(task, args.mode, path=args.dragged_path))
    elif args.dragged_video:
        task = args.task or DEFAULT_VIDEO_TASK
        print(read_dragged_video(task, args.mode, path=args.dragged_path))
    elif args.clipboard:
        task = args.task or DEFAULT_TASK
        print(read_clipboard_image(task, args.mode))
    elif args.video:
        task = args.task or DEFAULT_VIDEO_TASK
        print(read_video(args.video, task, args.mode))
    elif args.audio:
        task = args.task or DEFAULT_AUDIO_TASK
        print(read_audio(args.audio, task, args.mode))
    elif not args.image:
        raise ReadImageError("请提供 --image、--video 或 --audio 参数。")
    elif len(args.image) == 1:
        task = args.task or DEFAULT_TASK
        print(read_image(args.image[0], task, args.mode))
    else:
        task = args.task or DEFAULT_BATCH_TASK
        print(read_images_batch(args.image, task, args.mode, args.max_workers))
    return 0


def main() -> None:
    if len(sys.argv) > 1:
        sys.exit(run_cli(_build_parser(), _run_cli_handler))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
