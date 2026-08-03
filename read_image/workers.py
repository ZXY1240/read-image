from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from read_image.config import video_worker_count

video_executor = ThreadPoolExecutor(
    max_workers=video_worker_count(),
    thread_name_prefix="read-image-video",
)
atexit.register(video_executor.shutdown, wait=False)


def run_video_task(function: Any, *args: Any, **kwargs: Any) -> Any:
    return video_executor.submit(function, *args, **kwargs).result()
