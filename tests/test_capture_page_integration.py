from __future__ import annotations

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from omnimodal.mcp.capture_page_server import _capture_page


@pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_INTEGRATION") != "1",
    reason="Browser integration runs explicitly in CI.",
)
def test_capture_page_creates_real_screenshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_ALLOW_PRIVATE_URLS", "1")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = (
                b"<html><body style='margin:0'>"
                b"<h1 id='title'>Integration Test Page</h1>"
                b"<div id='panel'>Real browser screenshot test</div>"
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        output = asyncio.run(
            _capture_page(url, actions=None, viewport="800x600", output_dir=str(tmp_path))
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    line = output.splitlines()[0]
    path_text = line.split(". ", 1)[1]
    screenshot = Path(path_text)
    assert screenshot.is_file()
    assert screenshot.stat().st_size > 0
    with Image.open(screenshot) as image:
        image.load()
        assert image.width == 800
        assert image.height == 600
