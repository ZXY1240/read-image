from __future__ import annotations

import fnmatch
import os
import subprocess
import time
from pathlib import Path

from PIL import Image

from omnimodal.config import drag_dirs, drag_patterns, drag_window_minutes
from omnimodal.errors import ReadImageError, tr

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".tmp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".flac", ".amr", ".wma", ".tmp"}
MAX_DRAG_CANDIDATES = 20


def _is_image_file(path: Path) -> bool:
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    try:
        with Image.open(path) as image:
            image.load()
        return True
    except Exception:
        return False


def _is_video_file(path: Path) -> bool:
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    if path.suffix.lower() != ".tmp":
        return True
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_audio_file(path: Path) -> bool:
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        return False
    if path.suffix.lower() != ".tmp":
        return True
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False
    if path.suffix.lower() != ".tmp":
        return True
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _matches_drag_pattern(path: Path) -> bool:
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in drag_patterns())


def scan_dragged_media(kind: str) -> list[Path]:
    window_sec = drag_window_minutes() * 60
    now = time.time()
    cutoff = now - window_sec
    candidates: list[Path] = []
    seen: set[str] = set()

    for raw_dir in drag_dirs():
        directory = Path(os.path.expandvars(raw_dir)).expanduser()
        if not directory.is_dir():
            continue
        try:
            entries = directory.iterdir()
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                if entry.resolve().as_posix() in seen:
                    continue
                if not _matches_drag_pattern(entry):
                    continue
                modified = entry.stat().st_mtime
                if modified < cutoff or modified > now:
                    continue
                if kind == "image" and not _is_image_file(entry):
                    continue
                if kind == "video" and not _is_video_file(entry):
                    continue
                if kind == "audio" and not _is_audio_file(entry):
                    continue
                seen.add(entry.resolve().as_posix())
                candidates.append(entry.resolve())
            except OSError:
                continue

    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[:MAX_DRAG_CANDIDATES]


def resolve_dragged_path(raw_path: str, kind: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    candidates = scan_dragged_media(kind)
    if path not in candidates:
        raise ReadImageError(
            tr(
                f"指定文件不在拖拽候选列表中：{path}。Claude 桌面端拖入的媒体不落盘，"
                f"请改用 read_clipboard_image 或提供明确文件路径调用 read_image/read_video。",
                f"Selected file is not in the dragged media candidates: {path}. "
                f"Dragged media in Claude Desktop is not written to disk; use "
                f"read_clipboard_image or provide an explicit path to the media tool.",
            )
        )
    return path
