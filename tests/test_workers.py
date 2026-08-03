from __future__ import annotations

from read_image.workers import run_video_task


def test_run_video_task_returns_result() -> None:
    assert run_video_task(lambda: "ok") == "ok"
