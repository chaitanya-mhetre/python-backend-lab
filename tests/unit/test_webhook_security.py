from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest

from flowforge.security.webhooks import (
    SecretBox,
    UnsafeWebhookURLError,
    check_url,
    new_signing_secret,
    sign,
    verify,
)
from flowforge.worker.webhooks import RETRY_SCHEDULE, next_retry_delay

KEY = "HJjtDt5BGKz2BlyOyjuIkp6bA6TYT9sGhqIGU6VKJKQ="


def test_signature_roundtrip_and_tamper_detection() -> None:
    secret, body = new_signing_secret(), b'{"id":"1"}'
    sig = sign(secret, 1_700_000_000, body)
    assert sig.startswith("v1=")
    assert verify(secret, 1_700_000_000, body, sig, now=1_700_000_010)
    assert not verify(secret, 1_700_000_000, body + b" ", sig, now=1_700_000_010)
    assert not verify("whsec_other", 1_700_000_000, body, sig, now=1_700_000_010)


def test_old_signatures_are_rejected_to_stop_replays() -> None:
    secret, body = new_signing_secret(), b"{}"
    sig = sign(secret, 1_000, body)
    assert not verify(secret, 1_000, body, sig, now=1_000 + 301)


def test_secret_box_encrypts() -> None:
    box = SecretBox(KEY)
    token = box.encrypt("whsec_abc")
    assert "whsec_abc" not in token
    assert box.decrypt(token) == "whsec_abc"


async def fake_resolver(host: str) -> list[str]:
    table = {
        "hooks.example.com": ["93.184.216.34"],
        "internal.example.com": ["10.0.0.5"],
        "mixed.example.com": ["93.184.216.34", "127.0.0.1"],
        "metadata.example.com": ["169.254.169.254"],
    }
    if host in table:
        return table[host]
    raise OSError("NXDOMAIN")


async def test_public_https_url_allowed() -> None:
    await check_url("https://hooks.example.com/in", resolver=fake_resolver)


@pytest.mark.parametrize(
    "url",
    [
        "https://internal.example.com/x",  # private range
        "https://mixed.example.com/x",  # any private answer is enough to refuse
        "https://metadata.example.com/latest",  # cloud metadata (link-local)
        "http://hooks.example.com/x",  # plain http
        "https://user:pw@hooks.example.com/x",  # credentials in URL
        "https://nope.example.com/x",  # unresolvable
        "ftp://hooks.example.com/x",
    ],
)
async def test_unsafe_urls_rejected(url: str) -> None:
    with pytest.raises(UnsafeWebhookURLError):
        await check_url(url, resolver=fake_resolver)


def test_retry_schedule() -> None:
    delays = [next_retry_delay(n) for n in range(1, 7)]
    assert delays == [*RETRY_SCHEDULE, None]
    assert RETRY_SCHEDULE[0] == timedelta(minutes=1) and RETRY_SCHEDULE[-1] == timedelta(hours=12)


def test_example_receiver_verifies_our_signature() -> None:
    path = Path(__file__).parents[2] / "examples" / "verify_webhook.py"
    spec = importlib.util.spec_from_file_location("verify_webhook", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secret, body, ts = new_signing_secret(), b'{"id":"abc"}', 1_700_000_000
    headers = {"X-Flowforge-Timestamp": str(ts), "X-Flowforge-Signature": sign(secret, ts, body)}
    assert module.verify_request(secret, headers, body, now=ts + 5)
    assert not module.verify_request(secret, headers, body, now=ts + 3600)
