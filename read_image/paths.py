from __future__ import annotations

import os
import tempfile
from pathlib import Path

from read_image.errors import ReadImageError, tr


def _allowed_output_roots() -> list[Path]:
    roots = [
        Path(tempfile.gettempdir()).resolve(),
        Path.cwd().resolve(),
    ]
    configured = os.environ.get("READ_IMAGE_ALLOWED_OUTPUT_DIRS", "").strip()
    for raw in configured.split(";"):
        raw = raw.strip()
        if raw:
            roots.append(Path(raw).expanduser().resolve())
    return roots


def ensure_allowed_output_dir(
    output_dir: str | None,
    *,
    default_dir: str | None = None,
    extra_allowed_roots: list[str] | None = None,
) -> Path:
    if output_dir:
        target = Path(output_dir).expanduser().resolve()
        allowed = _allowed_output_roots()
        if extra_allowed_roots:
            allowed += [
                Path(root).expanduser().resolve()
                for root in extra_allowed_roots
                if root.strip()
            ]
        if not any(
            target == root or target.is_relative_to(root) for root in allowed
        ):
            raise ReadImageError(
                tr(
                    "输出目录不在允许范围内。请使用临时目录、当前工作区，"
                    "或通过 READ_IMAGE_ALLOWED_OUTPUT_DIRS 添加允许目录。",
                    "Output directory is outside the allowed roots. Use a temp "
                    "directory, the current workspace, or add the directory to "
                    "READ_IMAGE_ALLOWED_OUTPUT_DIRS.",
                )
            )
        target.mkdir(parents=True, exist_ok=True)
        return target
    if default_dir:
        target = Path(default_dir).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target
    return Path(tempfile.mkdtemp(prefix="read-image-output-"))
