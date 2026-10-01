"""Inbound webhook receiver with the three checks that matter in production:

1. Signature (HMAC-SHA256 over the raw body) so nobody can forge events.
2. Timestamp window so a captured request cannot be replayed later.
3. Idempotency by event id, because providers retry and deliver duplicates.

Standard library only. Run it: WEBHOOK_SECRET=dev python webhooks.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_SKEW_SECONDS = 300


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    return hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class WebhookVerifier:
    def __init__(self, secret: bytes, seen_capacity: int = 10_000):
        self.secret = secret
        self.seen: dict[str, float] = {}
        self.capacity = seen_capacity

    def check(self, timestamp: str, signature: str, body: bytes, now: float | None = None) -> tuple[int, str]:
        now = now or time.time()
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            return 400, "bad timestamp"
        if abs(now - ts) > MAX_SKEW_SECONDS:
            return 401, "stale request"
        if not hmac.compare_digest(sign(self.secret, timestamp, body), signature or ""):
            return 401, "bad signature"
        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            return 400, "bad json"
        event_id = str(event.get("id", ""))
        if not event_id:
            return 400, "missing id"
        if event_id in self.seen:
            return 200, "duplicate ignored"
        if len(self.seen) >= self.capacity:
            self.seen.pop(next(iter(self.seen)))
        self.seen[event_id] = now
        return 202, "accepted"


def make_handler(verifier: WebhookVerifier, on_event):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            code, msg = verifier.check(self.headers.get("X-Timestamp"), self.headers.get("X-Signature"), body)
            if code == 202:
                on_event(json.loads(body))  # in production: enqueue and return fast
            self.send_response(code)
            self.end_headers()
            self.wfile.write(msg.encode())

        def log_message(self, *args):
            pass

    return Handler


if __name__ == "__main__":
    secret = os.environ.get("WEBHOOK_SECRET", "").encode()
    if not secret:
        raise SystemExit("Set WEBHOOK_SECRET")
    server = HTTPServer(("127.0.0.1", 8088), make_handler(WebhookVerifier(secret), print))
    print("listening on http://127.0.0.1:8088")
    server.serve_forever()
