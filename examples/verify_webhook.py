"""How a receiver verifies a Flowforge webhook (copy this into your service).

Run a tiny receiver locally:
    uv run python examples/verify_webhook.py  # listens on :9000, prints verified events
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer

TOLERANCE_SECONDS = 300


def verify_request(
    secret: str, headers: Mapping[str, str], body: bytes, now: int | None = None
) -> bool:
    """True if the body was signed by Flowforge with ``secret`` in the last 5 minutes."""
    try:
        timestamp = int(headers["X-Flowforge-Timestamp"])
        signature = headers["X-Flowforge-Signature"]
    except (KeyError, ValueError):
        return False
    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > TOLERANCE_SECONDS:  # replayed or badly delayed
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    # compare_digest: constant-time, so attackers can't guess the signature byte by byte
    return hmac.compare_digest(f"v1={expected.hexdigest()}", signature)


SECRET = "whsec_replace_me"  # the secret shown once when you created the webhook


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        ok = verify_request(SECRET, dict(self.headers.items()), body)
        self.send_response(200 if ok else 401)
        self.end_headers()
        if ok:
            # Deduplicate on the event id: deliveries can arrive more than once (retries).
            print("verified", json.loads(body)["id"], self.headers["X-Flowforge-Event"])


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 9000), Handler).serve_forever()
