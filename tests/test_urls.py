from __future__ import annotations

import socket

import pytest

from read_image.errors import ReadImageError
from read_image.urls import validate_remote_url


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

    monkeypatch.setattr("read_image.urls.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ReadImageError):
        validate_remote_url("http://private.example.test/")


def test_allow_private_urls_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READ_IMAGE_ALLOW_PRIVATE_URLS", "1")
    assert validate_remote_url("http://127.0.0.1/") == "http://127.0.0.1/"
