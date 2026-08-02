from __future__ import annotations

import threading
import time

import pytest

from read_image.errors import ReadImageError
from read_image.mcp import read_image_server
from read_image.mcp.read_image_server import _batch_timeout_sec
from read_image.profiles import profile_for_mode


def test_batch_timeout_helper_uses_profile_plus_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("READ_IMAGE_BATCH_TIMEOUT_SEC", raising=False)
    assert (
        _batch_timeout_sec("standard")
        == profile_for_mode("standard").timeout_sec + 30
    )


def test_batch_timeout_helper_uses_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_BATCH_TIMEOUT_SEC", "7")
    assert _batch_timeout_sec("standard") == 7


def test_batch_returns_partial_failures_instead_of_dropping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_prepare(path: str) -> tuple[bytes, str]:
        if "missing" in path:
            raise ReadImageError("missing file")
        return b"image", "image/png"

    monkeypatch.setattr(read_image_server, "prepare_image", fake_prepare)
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
        "prepare_image",
        lambda path: (b"image", "image/png"),
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
