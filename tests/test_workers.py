"""Tests for the video worker pool (workers.py)."""

from __future__ import annotations

import threading
import time

from omnimodal.workers import run_video_task


def test_run_video_task_returns_result() -> None:
    assert run_video_task(lambda: "ok") == "ok"


def test_run_video_task_passes_args() -> None:
    result = run_video_task(lambda a, b: f"{a}{b}", "x", "y")
    assert result == "xy"


def test_run_video_task_propagates_exception() -> None:
    def boom() -> str:
        raise RuntimeError("video failed")

    raised = False
    try:
        run_video_task(boom)
    except RuntimeError as exc:
        raised = True
        assert str(exc) == "video failed"
    assert raised


def test_run_video_task_concurrent_tasks_isolate_failures() -> None:
    """并发任务中单个失败不影响其他任务。"""

    def ok(i: int) -> str:
        time.sleep(0.05)
        return f"ok-{i}"

    def bad() -> str:
        raise ValueError("bad task")

    results: list[str] = []

    def run_ok(i: int) -> None:
        results.append(run_video_task(ok, i))

    threads = [
        threading.Thread(target=run_ok, args=(1,)),
        threading.Thread(target=run_ok, args=(2,)),
    ]
    for t in threads:
        t.start()
    bad_raised = False
    try:
        run_video_task(bad)
    except ValueError:
        bad_raised = True
    for t in threads:
        t.join()
    assert bad_raised
    assert sorted(results) == ["ok-1", "ok-2"]
