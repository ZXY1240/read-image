from __future__ import annotations

import queue
import sys
import threading
import time
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
    env_int,
)
from read_image.errors import ReadImageError, tr
from read_image.logging import configure_logging
from read_image.media import analyze_video, prepare_image
from read_image.profiles import profile_for_mode

mcp = FastMCP("read-image")
logger = configure_logging("read-image-vision")
_image_cache = ImageCache(cache_max_entries())


def _run_image_with_cache(
    image_bytes: bytes,
    mime_type: str,
    task: str,
    mode: str,
    gate: api.ConcurrencyGate | None = None,
) -> str:
    profile = profile_for_mode(mode)
    key = image_cache_key(
        image_bytes,
        task,
        profile.key,
        api.default_client.model,
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
    )
    _image_cache.put(key, result)
    return result


@mcp.tool()
def read_image(
    image: Annotated[str, Field(description="本地图片文件的绝对路径。")],
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
    """读取本地图片，并按 task 和 mode 调用豆包视觉模型提取结果。"""
    effective_task = task.strip() if task and task.strip() else DEFAULT_TASK
    profile_for_mode(mode)
    image_bytes, mime_type = prepare_image(image)
    return _run_image_with_cache(image_bytes, mime_type, effective_task, mode)


@mcp.tool()
def read_video(
    video: Annotated[
        str,
        Field(description="本地视频文件绝对路径，或 http(s) 视频 URL。"),
    ],
    task: Annotated[
        str,
        Field(
            description=(
                "本次要从视频中提取或分析的具体内容；"
                "未传时默认详细描述视频内容。"
            )
        ),
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
    return analyze_video(video, effective_task, mode)


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
    return profile_for_mode(mode).timeout_sec + 30


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
            sections.append(
                f"## 第 {index + 1}/{total} 张：{filename}\n\n{content}"
            )
    return "\n\n".join(sections)


@mcp.tool()
def read_images_batch(
    images: Annotated[
        list[str],
        Field(description="本地图片绝对路径列表，至少 1 张；结果按传入顺序汇总。"),
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
    gate = api.ConcurrencyGate(workers)
    timeout_sec = _batch_timeout_sec(mode)
    work_queue: queue.Queue[tuple[int, str]] = queue.Queue()
    results: list[tuple[int, str, str | None, str | None] | None] = [None] * len(images)
    result_set = [False] * len(images)
    events = [threading.Event() for _ in images]
    deadlines = [time.monotonic() + timeout_sec for _ in images]

    def record(
        index: int,
        path: str,
        content: str | None,
        error: str | None,
    ) -> None:
        if result_set[index]:
            return
        results[index] = (index, path, content, error)
        result_set[index] = True
        events[index].set()

    def worker() -> None:
        while True:
            try:
                index, path = work_queue.get_nowait()
            except queue.Empty:
                return
            try:
                image_bytes, mime_type = prepare_image(path)
                content = _run_image_with_cache(
                    image_bytes,
                    mime_type,
                    effective_task,
                    mode,
                    gate=gate,
                )
                record(index, path, content, None)
            except ReadImageError as exc:
                record(index, path, None, str(exc))
            except Exception as exc:
                record(index, path, None, f"未知错误：{exc}")
            finally:
                work_queue.task_done()

    for index, path in enumerate(images):
        work_queue.put((index, str(path).strip()))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for thread in threads:
        thread.start()

    for index in range(len(images)):
        remaining = deadlines[index] - time.monotonic()
        if remaining > 0:
            events[index].wait(remaining)
        if not result_set[index]:
            record(
                index,
                str(images[index]).strip(),
                None,
                tr(
                    f"批量识别超时（超过 {timeout_sec} 秒）。",
                    f"Batch image timed out after {timeout_sec}s.",
                ),
            )

    for thread in threads:
        thread.join(timeout=0)

    completed = [result for result in results if result is not None]
    if not completed:
        raise ReadImageError("批量识别没有返回任何结果。")
    successful = [result for result in completed if result[2] is not None]
    if not successful:
        errors = "; ".join(str(result[3] or "未知错误") for result in completed[:5])
        raise ReadImageError(f"批量识别全部失败：{errors}")
    return _format_batch_results(completed)


def _run_cli() -> None:
    import argparse

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass

    parser = argparse.ArgumentParser(
        description="Read local images or videos via the Doubao vision API."
    )
    parser.add_argument(
        "--image",
        action="append",
        help="Absolute path to a local image; repeat for batch mode.",
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
    args = parser.parse_args()
    try:
        if args.video:
            task = args.task or DEFAULT_VIDEO_TASK
            print(read_video(args.video, task, args.mode))
        elif not args.image:
            parser.error("请提供 --image 或 --video 参数。")
        elif len(args.image) == 1:
            task = args.task or DEFAULT_TASK
            print(read_image(args.image[0], task, args.mode))
        else:
            task = args.task or DEFAULT_BATCH_TASK
            print(read_images_batch(args.image, task, args.mode, args.max_workers))
    except ReadImageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    if len(sys.argv) > 1:
        _run_cli()
    else:
        mcp.run()


if __name__ == "__main__":
    main()
