from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from read_image import drag
from read_image.errors import ReadImageError
from read_image.mcp import read_image_server


def _png_bytes(size: tuple[int, int] = (20, 10)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")
    return buffer.getvalue()


def _write_image(path: Path, modified: float | None = None) -> None:
    path.write_bytes(_png_bytes())
    if modified is not None:
        os.utime(path, (modified, modified))


def test_scan_dragged_image_matches_window_and_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drag, "drag_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(drag, "drag_window_minutes", lambda: 5)
    now = time.time()
    _write_image(tmp_path / "pasted_image.png", now)
    _write_image(tmp_path / "old_image.png", now - 600)
    _write_image(tmp_path / "other.png", now)
    (tmp_path / "notes.txt").write_text("not image", encoding="utf-8")

    candidates = drag.scan_dragged_media("image")
    assert len(candidates) == 1
    assert candidates[0].name == "pasted_image.png"


def test_scan_dragged_image_sniffs_tmp_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drag, "drag_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(drag, "drag_window_minutes", lambda: 5)
    _write_image(tmp_path / "random-image.tmp")
    (tmp_path / "random-text.tmp").write_text("not an image", encoding="utf-8")

    candidates = drag.scan_dragged_media("image")
    assert len(candidates) == 1
    assert candidates[0].name == "random-image.tmp"


def test_scan_dragged_video_matches_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drag, "drag_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(drag, "drag_window_minutes", lambda: 5)
    (tmp_path / "pasted_video.mp4").write_bytes(b"video-bytes")

    candidates = drag.scan_dragged_media("video")
    assert len(candidates) == 1
    assert candidates[0].name == "pasted_video.mp4"


def test_scan_dragged_media_sorts_by_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drag, "drag_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(drag, "drag_window_minutes", lambda: 5)
    now = time.time()
    _write_image(tmp_path / "pasted_a.png", now - 10)
    _write_image(tmp_path / "pasted_b.png", now)

    candidates = drag.scan_dragged_media("image")
    assert [path.name for path in candidates] == ["pasted_b.png", "pasted_a.png"]


def test_read_dragged_image_single_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "pasted_image.png"
    monkeypatch.setattr(
        read_image_server,
        "scan_dragged_media",
        lambda kind: [image_path],
    )
    monkeypatch.setattr(
        read_image_server,
        "read_image",
        lambda image, task, mode: f"result:{image}",
    )
    result = read_image_server.read_dragged_image("task", "quick")
    assert str(image_path) in result
    assert "result:" in result


def test_read_dragged_image_multiple_candidates_require_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "pasted_a.png"
    second = tmp_path / "pasted_b.png"
    monkeypatch.setattr(
        read_image_server,
        "scan_dragged_media",
        lambda kind: [first, second],
    )
    result = read_image_server.read_dragged_image("task", "quick")
    assert "找到多个" in result
    assert str(first) in result
    assert str(second) in result


def test_read_dragged_image_uses_explicit_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "pasted_image.png"
    monkeypatch.setattr(
        read_image_server,
        "resolve_dragged_path",
        lambda raw_path, kind: image_path,
    )
    monkeypatch.setattr(
        read_image_server,
        "read_image",
        lambda image, task, mode: f"result:{image}",
    )
    result = read_image_server.read_dragged_image("task", "quick", path=str(image_path))
    assert "result:" in result


def test_read_dragged_image_no_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        read_image_server,
        "scan_dragged_media",
        lambda kind: [],
    )
    with pytest.raises(ReadImageError) as exc_info:
        read_image_server.read_dragged_image("task", "quick")
    assert "没有找到最近拖拽的图片" in str(exc_info.value)


def test_read_dragged_video_single_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "pasted_video.mp4"
    monkeypatch.setattr(
        read_image_server,
        "scan_dragged_media",
        lambda kind: [video_path],
    )
    monkeypatch.setattr(
        read_image_server,
        "read_video",
        lambda video, task, mode: f"result:{video}",
    )
    result = read_image_server.read_dragged_video("task", "quick")
    assert str(video_path) in result
    assert "result:" in result
