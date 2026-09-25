"""Webhook security: signing, secret encryption and SSRF protection.

Signing (what receivers verify):
    X-Flowforge-Timestamp: <unix seconds>
    X-Flowforge-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
Including the timestamp in the signed string lets receivers reject old messages (replay
protection): a captured request can't be re-sent an hour later with a valid signature.

SSRF: webhook URLs are user input, and our server makes the request. Without a guard, a user
could point a webhook at http://169.254.169.254/ (cloud metadata) or internal services. We
resolve the hostname and refuse private, loopback, link-local and reserved addresses, both
when the webhook is saved and again right before each delivery (DNS can change).
Limitation: a DNS answer could still change between our check and httpx's own lookup (DNS
rebinding). Fully closing that needs connecting to the checked IP; see docs/security.md.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import anyio
from cryptography.fernet import Fernet

from flowforge.domain.errors import DomainError

SIGNATURE_VERSION = "v1"


class UnsafeWebhookURLError(DomainError):
    code = "unsafe_webhook_url"


def new_signing_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"{SIGNATURE_VERSION}={mac.hexdigest()}"


def verify(
    secret: str, timestamp: int, body: bytes, signature: str, *, now: int, tolerance: int = 300
) -> bool:
    if abs(now - timestamp) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


class SecretBox:
    """Symmetric encryption for secrets we must be able to read back (unlike passwords)."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()


Resolver = Callable[[str], Awaitable[list[str]]]


async def system_resolver(host: str) -> list[str]:
    infos = await anyio.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


async def check_url(url: str, *, resolver: Resolver, allow_private: bool = False) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise UnsafeWebhookURLError("webhook URL must be http(s) with a hostname")
    if parts.scheme == "http" and not allow_private:
        raise UnsafeWebhookURLError("webhook URL must use https")
    if parts.username or parts.password:
        raise UnsafeWebhookURLError("credentials in webhook URLs are not allowed")
    if allow_private:
        return
    try:
        ips = await resolver(parts.hostname)
    except OSError as exc:
        raise UnsafeWebhookURLError(f"cannot resolve {parts.hostname}") from exc
    if not ips or not all(_is_public(ip) for ip in ips):
        raise UnsafeWebhookURLError(f"{parts.hostname} resolves to a non-public address")
