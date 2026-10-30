"""SSRF protection for user-configured outbound URLs (tenant webhooks)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.config import settings


class UnsafeURLError(ValueError):
    pass


async def assert_public_url(url: str) -> None:
    """Reject non-http(s) URLs and, in production, hosts resolving to private,
    loopback, link-local or reserved addresses (cloud metadata endpoints etc.)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURLError("Only absolute http(s) URLs are allowed")
    if not settings.is_production:
        return  # local development commonly targets localhost receivers
    if parts.scheme != "https":
        raise UnsafeURLError("Webhooks must use https in production")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeURLError(f"Cannot resolve host {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeURLError("Webhook host resolves to a non-public address")
