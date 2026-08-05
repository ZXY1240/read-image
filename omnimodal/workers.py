from __future__ import annotations

import atexit
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from omnimodal.config import video_worker_count

video_executor = ThreadPoolExecutor(
    max_workers=video_worker_count(),
    thread_name_prefix="omnimodal-video",
)
atexit.register(video_executor.shutdown, wait=False)


def run_video_task(function: Callable[..., str], *args: Any, **kwargs: Any) -> str:
    return video_executor.submit(function, *args, **kwargs).result()
