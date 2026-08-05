from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from omnimodal import http
from omnimodal.errors import ReadImageError
from omnimodal.http import SafeHTTPTransport
from omnimodal.urls import validate_remote_url


def test_allows_public_http_url() -> None:
    assert validate_remote_url("https://8.8.8.8/path") == "https://8.8.8.8/path"


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ReadImageError):
        validate_remote_url("file:///etc/passwd")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/",
    ],
)
def test_rejects_local_private_and_metadata_urls(url: str) -> None:
    with pytest.raises(ReadImageError):
        validate_remote_url(url)


def test_blocks_hostname_resolving_to_private_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("192.168.1.9", 80),
            )
        ]

    monkeypatch.setattr("omnimodal.urls.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ReadImageError):
        validate_remote_url("http://private.example.test/")


def test_allow_private_urls_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIMODAL_ALLOW_PRIVATE_URLS", "1")
    assert validate_remote_url("http://127.0.0.1/") == "http://127.0.0.1/"


def test_safe_transport_validates_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(url: str) -> str:
        raise ReadImageError("blocked")

    monkeypatch.setattr(http, "validate_remote_url", reject)
    transport = SafeHTTPTransport()
    request = httpx.Request("GET", "http://127.0.0.1/")
    with pytest.raises(ReadImageError):
        transport.handle_request(request)


def test_safe_transport_revalidates_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/end")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    calls: list[str] = []
    monkeypatch.setattr(
        http,
        "validate_remote_url",
        lambda url: calls.append(str(url)) or str(url),
    )
    client = httpx.Client(transport=SafeHTTPTransport(), follow_redirects=True)
    try:
        response = client.get(f"http://127.0.0.1:{server.server_port}/start")
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=5)
    assert response.status_code == 200
    assert len(calls) == 2
    assert calls[0].endswith("/start")
    assert calls[1].endswith("/end")
