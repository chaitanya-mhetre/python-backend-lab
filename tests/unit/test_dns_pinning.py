"""DNS-rebinding protection for webhook delivery (issue #3).

Attack: the attacker's DNS answers the SSRF check with a public IP, then answers the HTTP
client's own lookup with 10.0.0.5. Pinning means there is no second lookup to lie to.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx
import pytest

from flowforge.security.webhooks import (
    PinnedDNSBackend,
    PinnedTransport,
    UnsafeWebhookURLError,
    check_url,
    pinned_transport,
)

PUBLIC, PRIVATE = "93.184.216.34", "10.0.0.5"


class RebindingResolver:
    """First answer public, every later answer private: a classic rebinding DNS server."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, host: str) -> list[str]:
        self.calls += 1
        return [PUBLIC] if self.calls == 1 else [PRIVATE]


class RecordingStream(httpcore.AsyncNetworkStream):
    def __init__(self, log: dict[str, Any]) -> None:
        self._log = log

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        raise httpcore.ReadError("test stream")

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        raise httpcore.WriteError("test stream")

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self._log["server_hostname"] = server_hostname
        raise httpcore.ConnectError("stop after recording the TLS handshake parameters")

    def get_extra_info(self, info: str) -> Any:
        return None


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Stands in for the real network: records where we *would* have connected."""

    def __init__(self) -> None:
        self.log: dict[str, Any] = {}

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.log["connect"] = (host, port)
        return RecordingStream(self.log)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("not used")

    async def sleep(self, seconds: float) -> None:
        return None


async def test_rebinding_dns_cannot_redirect_the_connection() -> None:
    url = "https://rebind.example.com/hook"
    resolver = RebindingResolver()
    ips = await check_url(url, resolver=resolver)  # the check sees the public answer
    assert ips == [PUBLIC]

    backend = RecordingBackend()
    transport = PinnedTransport({"rebind.example.com": ips[0]}, inner=backend)
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post(url, content=b"{}")

    # We connected to the vetted public IP, and nobody asked DNS a second time,
    # so the private answer the attacker prepared was never used.
    assert backend.log["connect"] == (PUBLIC, 443)
    assert resolver.calls == 1


async def test_second_check_would_have_seen_the_private_answer() -> None:
    # Why the old design was vulnerable: any second lookup gets the attacker's private IP.
    resolver = RebindingResolver()
    await check_url("https://rebind.example.com/", resolver=resolver)
    with pytest.raises(UnsafeWebhookURLError):
        await check_url("https://rebind.example.com/", resolver=resolver)


async def test_tls_still_verifies_the_hostname_not_the_ip() -> None:
    backend = RecordingBackend()
    transport = PinnedTransport({"hooks.example.com": PUBLIC}, inner=backend)
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://hooks.example.com/")
    assert backend.log["connect"] == (PUBLIC, 443)
    assert backend.log["server_hostname"] == "hooks.example.com"  # SNI + certificate check


async def test_unvetted_hosts_are_refused() -> None:
    backend = PinnedDNSBackend({"hooks.example.com": PUBLIC}, inner=RecordingBackend())
    with pytest.raises(httpcore.ConnectError, match="unvetted"):
        await backend.connect_tcp("evil.example.com", 443)
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_unix_socket("/var/run/docker.sock")


def test_pinned_transport_helper() -> None:
    transport = pinned_transport("https://hooks.example.com/x", [PUBLIC, "93.184.216.35"])
    assert isinstance(transport, PinnedTransport)
    assert transport.pins == {"hooks.example.com": PUBLIC}
    assert pinned_transport("https://hooks.example.com/x", []) is None  # allow-private dev mode
