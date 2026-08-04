from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from omnimodal.mcp.windows_capture_server import capture_windows, list_windows


@pytest.mark.skipif(
    not (os.name == "nt" and os.environ.get("RUN_WINDOWS_CAPTURE_INTEGRATION") == "1"),
    reason="Windows capture integration runs explicitly in Windows CI.",
)
def test_windows_capture_creates_real_screenshot(
    tmp_path: Path,
) -> None:
    notepad = subprocess.Popen(["notepad.exe"])
    try:
        time.sleep(2)
        titles = list_windows()
        candidates = [
            line.split(". ", 1)[1]
            for line in titles.splitlines()
            if "Notepad" in line or "记事本" in line
        ]
        if candidates:
            path_text = capture_windows(
                mode="window",
                window=candidates[0],
                output_dir=str(tmp_path),
            )
        else:
            path_text = capture_windows(mode="primary", output_dir=str(tmp_path))
    finally:
        notepad.terminate()

    screenshot = Path(path_text)
    assert screenshot.is_file()
    assert screenshot.stat().st_size > 0
    with Image.open(screenshot) as image:
        image.load()
        assert image.width > 0
        assert image.height > 0
