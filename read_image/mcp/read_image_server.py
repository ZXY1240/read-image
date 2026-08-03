from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from read_image import api
from read_image.cache import ImageCache, image_cache_key
from read_image.config import (
    DEFAULT_BATCH_TASK,
    DEFAULT_BATCH_WORKERS,
    DEFAULT_MODE,
    DEFAULT_TASK,
    DEFAULT_VIDEO_TASK,
    MAX_BATCH_WORKERS,
    cache_max_entries,
    cache_use_task,
    env_int,
)
from read_image.errors import ReadImageError, tr
from read_image.logging import configure_logging
from read_image.mcp.common import run_cli
from read_image.media import analyze_video, prepare_image_variants
from read_image.profiles import profile_for_mode
from read_image.workers import run_video_task

mcp = FastMCP("read-image")
logger = configure_logging("read-image-vision")
_image_cache = ImageCache(cache_max_entries())
BATCH_TIMEOUT_BUFFER_SEC = 30
CLIPBOARD_SAVE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "save_clipboard_image.ps1"


def _format_slice_results(results: list[str]) -> str:
    if len(results) == 1:
        return results[0]
    return "\n\n".join(
        f"## 第 {index + 1}/{len(results)} 段\n\n{result}" for index, result in enumerate(results)
    )


def _run_image_with_cache(
    image_bytes: bytes,
    mime_type: str,
    task: str,
    mode: str,
    gate: api.ConcurrencyGate | None = None,
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
        gate=gate,
        timeout_sec=timeout_sec,
    )
    _image_cache.put(key, result)
    return result


@mcp.tool()
def read_image(
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
) -> str:
    """读取本地图片、data URL 或 base64 图片，并按 task 和 mode 调用视觉模型。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_TASK
    profile_for_mode(mode)
    variants = prepare_image_variants(image)
    results = [
        _run_image_with_cache(image_bytes, mime_type, effective_task, mode)
        for image_bytes, mime_type in variants
    ]
    return _format_slice_results(results)


@mcp.tool()
def read_clipboard_image(
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
) -> str:
    """保存并读取 Windows 剪贴板图片，直接返回视觉模型结果。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_TASK
    profile_for_mode(mode)
    if os.name != "nt":
        raise ReadImageError(
            tr(
                "read_clipboard_image 仅支持 Windows。",
                "read_clipboard_image is only supported on Windows.",
            )
        )
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


@mcp.tool()
def read_video(
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
) -> str:
    """读取本地视频或视频 URL，并按 task 和 mode 调用豆包视频理解。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_VIDEO_TASK
    profile_for_mode(mode)
    return run_video_task(analyze_video, video, effective_task, mode)


def _clamp_workers(value: int | None) -> int:
    if value is None:
        requested = env_int("READ_IMAGE_BATCH_WORKERS", DEFAULT_BATCH_WORKERS)
    else:
        try:
            requested = int(value)
        except (TypeError, ValueError):
            requested = DEFAULT_BATCH_WORKERS
    return min(MAX_BATCH_WORKERS, max(1, requested))


def _batch_timeout_sec(mode: str) -> int:
    configured = env_int("READ_IMAGE_BATCH_TIMEOUT_SEC", 0)
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


@mcp.tool()
def read_images_batch(
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
) -> str:
    """批量读取本地图片，使用共享任务队列并行调用视觉模型并按原顺序返回。"""
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
        thread_name_prefix="read-image-batch",
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read local images or videos via the Doubao vision API."
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
    return parser


def _run_cli_handler(args: argparse.Namespace) -> int:
    if args.clipboard:
        task = args.task or DEFAULT_TASK
        print(read_clipboard_image(task, args.mode))
    elif args.video:
        task = args.task or DEFAULT_VIDEO_TASK
        print(read_video(args.video, task, args.mode))
    elif not args.image:
        raise ReadImageError("请提供 --image 或 --video 参数。")
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
