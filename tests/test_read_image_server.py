from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

from read_image import api
from read_image.errors import ReadImageError
from read_image.mcp import read_image_server
from read_image.mcp.read_image_server import _batch_timeout_sec
from read_image.profiles import profile_for_mode
from read_image.providers import base as provider_base


def test_batch_timeout_helper_uses_profile_plus_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("READ_IMAGE_BATCH_TIMEOUT_SEC", raising=False)
    assert _batch_timeout_sec("standard") == profile_for_mode("standard").timeout_sec + 30


def test_batch_timeout_helper_uses_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_BATCH_TIMEOUT_SEC", "7")
    assert _batch_timeout_sec("standard") == 7


def test_batch_returns_partial_failures_instead_of_dropping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_prepare(path: str) -> list[tuple[bytes, str]]:
        if "missing" in path:
            raise ReadImageError("missing file")
        return [(b"image", "image/png")]

    monkeypatch.setattr(read_image_server, "prepare_image_variants", fake_prepare)
    monkeypatch.setattr(
        read_image_server,
        "_run_image_with_cache",
        lambda *args, **kwargs: "ok",
    )

    result = read_image_server.read_images_batch(
        ["ok.png", "missing.png"],
        "task",
        "quick",
        1,
    )
    assert "ok" in result
    assert "missing file" in result


def test_batch_times_out_individual_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    monkeypatch.setattr(
        read_image_server,
        "prepare_image_variants",
        lambda path: [(b"image", "image/png")],
    )

    def slow_call(*args: object, **kwargs: object) -> str:
        release.wait(5)
        return "late"

    monkeypatch.setattr(read_image_server, "_run_image_with_cache", slow_call)
    monkeypatch.setenv("READ_IMAGE_BATCH_TIMEOUT_SEC", "1")

    started = time.monotonic()
    with pytest.raises(ReadImageError) as exc_info:
        read_image_server.read_images_batch(
            ["slow.png"],
            "task",
            "quick",
            1,
        )
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 3
    assert "超时" in str(exc_info.value)


def test_batch_429_retries_locally_without_failing_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_base, "MAX_RATE_LIMIT_RETRIES", 1)
    monkeypatch.setattr(provider_base.time, "sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                json={"error": {"message": "slow"}},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        api,
        "default_client",
        api.VisionClient(client=client),
    )
    monkeypatch.setattr(
        read_image_server,
        "prepare_image_variants",
        lambda path: [(b"image", "image/png")],
    )

    result = read_image_server.read_images_batch(
        ["one.png"],
        "task",
        "quick",
        1,
    )
    assert "ok" in result
    assert calls == 2


def test_batch_does_not_misreport_slow_but_successful_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        read_image_server,
        "prepare_image_variants",
        lambda path: [(b"image", "image/png")],
    )

    def slow_call(*args: object, **kwargs: object) -> str:
        time.sleep(0.8)
        return "late-but-ok"

    monkeypatch.setattr(read_image_server, "_run_image_with_cache", slow_call)
    monkeypatch.setenv("READ_IMAGE_BATCH_TIMEOUT_SEC", "3")
    result = read_image_server.read_images_batch(
        ["slow.png"],
        "task",
        "quick",
        1,
    )
    assert "late-but-ok" in result


def test_read_image_merges_extreme_slice_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        read_image_server,
        "prepare_image_variants",
        lambda raw: [(b"one", "image/png"), (b"two", "image/png")],
    )
    monkeypatch.setattr(
        read_image_server,
        "_run_image_with_cache",
        lambda *args, **kwargs: "segment-result",
    )
    result = read_image_server.read_image("data:image/png;base64,AAAA", "task", "quick")
    assert "第 1/2 段" in result
    assert "第 2/2 段" in result
    assert result.count("segment-result") == 2


def test_read_clipboard_image_returns_vision_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clipboard_png = tmp_path / "clipboard.png"
    clipboard_png.write_bytes(b"fake-png")
    monkeypatch.setattr(
        read_image_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout=str(clipboard_png),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        read_image_server,
        "read_image",
        lambda image, task, mode: f"result:{image}:{task}:{mode}",
    )
    result = read_image_server.read_clipboard_image("task", "quick")
    assert result == f"result:{clipboard_png}:task:quick"


def test_read_clipboard_image_reports_missing_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        read_image_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr="Clipboard does not contain an image.",
        ),
    )
    with pytest.raises(ReadImageError) as exc_info:
        read_image_server.read_clipboard_image("task", "quick")
    assert "剪贴板中没有图片" in str(exc_info.value)


def test_read_clipboard_image_reports_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("read_image.mcp.read_image_server.os.name", "posix")
    with pytest.raises(ReadImageError) as exc_info:
        read_image_server.read_clipboard_image("task", "quick")
    assert "仅支持 Windows" in str(exc_info.value)
