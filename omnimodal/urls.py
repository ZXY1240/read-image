from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from omnimodal.config import allow_private_urls
from omnimodal.errors import ReadImageError, tr

_CLOUD_METADATA_IPS = {
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("100.100.100.201"),
    ipaddress.ip_address("100.100.100.202"),
    ipaddress.ip_address("100.100.100.203"),
    ipaddress.ip_address("169.254.169.254"),
}


def _is_blocked_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address in _CLOUD_METADATA_IPS
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def validate_remote_url(url: str, allow_private: bool | None = None) -> str:
    """Validate a remote http(s) URL before opening it."""
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ReadImageError(
            tr(
                "远程 URL 只支持 http/https。",
                "Remote URLs must use http or https.",
            )
        )
    hostname = parsed.hostname
    if not hostname:
        raise ReadImageError(
            tr(
                "远程 URL 缺少主机名。",
                "Remote URL is missing a hostname.",
            )
        )
    if allow_private is None:
        allow_private = allow_private_urls()
    if allow_private:
        return raw

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ReadImageError(
            tr(
                "远程 URL 无法解析。",
                "Remote URL could not be resolved.",
            )
        ) from exc

    for _, _, _, _, sockaddr in addresses:
        address_text = sockaddr[0]
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError:
            continue
        if _is_blocked_address(address):
            raise ReadImageError(
                tr(
                    "远程 URL 指向本机、内网或保留地址，已阻止。"
                    "如确需访问，可设置 READ_IMAGE_ALLOW_PRIVATE_URLS=1。",
                    "Remote URL points to a local, private, or reserved address "
                    "and was blocked. Set READ_IMAGE_ALLOW_PRIVATE_URLS=1 to allow it.",
                )
            )
    return raw
