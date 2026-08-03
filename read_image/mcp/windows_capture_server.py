from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
import tempfile
import time
from importlib import resources
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from read_image.errors import ReadImageError, WindowsCaptureError, tr
from read_image.logging import configure_logging
from read_image.mcp.common import LOCAL_WRITE_ANNOTATIONS, READ_ONLY_ANNOTATIONS, run_cli
from read_image.paths import ensure_allowed_output_dir

mcp = FastMCP("windows-capture")
logger = configure_logging("windows-capture")
WINDOWS_CAPTURE_TIMEOUT_SEC = 90


def _load_ps(name: str) -> str:
    return resources.files("read_image.assets.windows").joinpath(name).read_text(encoding="utf-8")


_LIST_WINDOWS_PS = _load_ps("list_windows.ps1")
_CAPTURE_PS = _load_ps("capture_windows.ps1")


def _require_windows() -> None:
    if os.name != "nt":
        raise WindowsCaptureError(
            tr(
                "capture_windows 仅支持 Windows 系统。",
                "capture_windows is only supported on Windows.",
            )
        )


def _run_powershell(
    script: str,
    timeout_sec: int = 90,
    env: dict[str, str] | None = None,
) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=timeout_sec,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise WindowsCaptureError(
            tr(
                "Windows 截图超时，请稍后重试。",
                "Windows screenshot timed out; retry later.",
            )
        ) from exc
    if result.returncode != 0:
        detail = _clean_powershell_error(result.stderr or result.stdout or "")
        if "window not found" in detail.lower():
            raise WindowsCaptureError(
                tr(
                    "找不到匹配窗口，请先用 list_windows 确认窗口标题。",
                    "No matching window found. Use list_windows first.",
                )
            )
        raise WindowsCaptureError(
            tr(
                f"Windows 截图失败：{detail}",
                f"Windows screenshot failed: {detail}",
            )
        )
    return result.stdout


def _clean_powershell_error(raw: str) -> str:
    match = re.search(r'<S S="Error">(.*?)</S>', raw, flags=re.DOTALL)
    if match:
        text = match.group(1)
    else:
        text = raw
    return text.replace("_x000D__x000A_", " ").replace("_x000D_", " ").strip()[:500]


def _default_capture_dir() -> Path:
    configured = os.environ.get("WINDOWS_CAPTURE_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(tempfile.mkdtemp(prefix="windows-capture-"))


def _safe_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    return cleaned.strip("._") or "window"


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def list_windows() -> str:
    """列出当前所有可见 Windows 窗口标题。"""
    _require_windows()
    output = _run_powershell(_LIST_WINDOWS_PS)
    titles = [line.strip() for line in output.splitlines() if line.strip()]
    if not titles:
        return tr("没有找到可见窗口。", "No visible windows found.")
    return "\n".join(f"{index}. {title}" for index, title in enumerate(titles, start=1))


@mcp.tool(annotations=LOCAL_WRITE_ANNOTATIONS)
def capture_windows(
    mode: Annotated[
        str,
        Field(
            description=("截图范围：full=所有显示器，primary=主显示器，window=指定窗口标题关键字。")
        ),
    ] = "full",
    window: Annotated[
        str | None,
        Field(description="mode=window 时使用的窗口标题关键字。"),
    ] = None,
    output_dir: Annotated[
        str | None,
        Field(description="截图输出目录；未传时使用临时目录。"),
    ] = None,
) -> str:
    """截取 Windows 全屏、主屏或指定窗口，并返回 PNG 路径。"""
    _require_windows()
    normalized_mode = str(mode or "full").strip().lower()
    if normalized_mode not in {"full", "primary", "window"}:
        raise WindowsCaptureError(
            tr(
                f"mode 只支持 full、primary、window，收到：{mode}",
                f"mode only supports full, primary, window; got: {mode}",
            )
        )
    if normalized_mode == "window" and not window:
        raise WindowsCaptureError(
            tr(
                "mode=window 时必须提供 window 参数。",
                "mode=window requires the window parameter.",
            )
        )

    if output_dir:
        try:
            extra_roots = [
                configured
                for configured in [os.environ.get("WINDOWS_CAPTURE_DIR", "")]
                if configured.strip()
            ]
            output_path = ensure_allowed_output_dir(
                output_dir,
                extra_allowed_roots=extra_roots,
            )
        except ReadImageError as exc:
            raise WindowsCaptureError(str(exc)) from exc
    else:
        output_path = _default_capture_dir()

    if normalized_mode == "window":
        filename = f"window-{_safe_filename(window or 'window')}-{int(time.time())}.png"
    else:
        filename = f"{normalized_mode}-{int(time.time())}.png"
    target = output_path / filename

    env = os.environ.copy()
    env["WINDOWS_CAPTURE_MODE"] = normalized_mode
    env["WINDOWS_CAPTURE_OUTPUT"] = str(target)
    env["WINDOWS_CAPTURE_WINDOW"] = window or ""
    _run_powershell(
        _CAPTURE_PS,
        timeout_sec=WINDOWS_CAPTURE_TIMEOUT_SEC,
        env=env,
    )

    if not target.is_file():
        raise WindowsCaptureError(
            tr(
                f"截图失败，未生成文件：{target}",
                f"Screenshot failed, file not generated: {target}",
            )
        )
    return str(target)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture native Windows screenshots.")
    parser.add_argument("--list-windows", action="store_true")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--mode", default="full")
    parser.add_argument("--window", default=None)
    parser.add_argument("--output-dir", default=None)
    return parser


def _run_cli_handler(args: argparse.Namespace) -> int:
    if args.list_windows:
        print(list_windows())
    elif args.capture:
        print(capture_windows(args.mode, args.window, args.output_dir))
    else:
        raise WindowsCaptureError(
            tr(
                "请提供 --list-windows 或 --capture 参数。",
                "Provide --list-windows or --capture.",
            )
        )
    return 0


def main() -> None:
    if len(sys.argv) > 1:
        sys.exit(run_cli(_build_parser(), _run_cli_handler))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
