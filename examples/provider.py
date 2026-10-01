"""Swappable provider layer.

The app talks to one small interface. Each external platform gets an adapter.
When a provider changes its API, raises prices or shuts down, you write one new
adapter and the rest of the application does not change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Contact:
    id: str
    display_name: str
    tenant: str


@dataclass(frozen=True)
class Message:
    contact_id: str
    text: str
    sent_at: datetime
    from_contact: bool


class MessagingProvider(Protocol):
    name: str

    def list_contacts(self, tenant: str) -> list[Contact]: ...
    def history(self, contact_id: str, limit: int = 50) -> list[Message]: ...
    def send(self, contact_id: str, text: str) -> str: ...  # returns provider message id


class ProviderA:
    """Adapter for a REST API that pages with `cursor` and returns `{"data": [...]}`."""
    name = "provider_a"

    def __init__(self, http):
        self.http = http  # injected client: easy to fake in tests

    def list_contacts(self, tenant):
        out, cursor = [], None
        while True:
            page = self.http.get("/contacts", params={"account": tenant, "cursor": cursor})
            out += [Contact(c["id"], c["name"], tenant) for c in page["data"]]
            cursor = page.get("next_cursor")
            if not cursor:
                return out

    def history(self, contact_id, limit=50):
        rows = self.http.get(f"/chats/{contact_id}/messages", params={"limit": limit})["data"]
        return [Message(contact_id, r["text"], datetime.fromisoformat(r["created_at"]), r["is_inbound"]) for r in rows]

    def send(self, contact_id, text):
        return self.http.post(f"/chats/{contact_id}/messages", json={"text": text})["id"]


class ProviderB:
    """Adapter for a different API: offset paging, other field names, epoch timestamps."""
    name = "provider_b"

    def __init__(self, http):
        self.http = http

    def list_contacts(self, tenant):
        out, offset = [], 0
        while True:
            rows = self.http.get("/v2/users", params={"owner": tenant, "offset": offset, "limit": 100})["users"]
            out += [Contact(str(r["user_id"]), r["username"], tenant) for r in rows]
            if len(rows) < 100:
                return out
            offset += 100

    def history(self, contact_id, limit=50):
        rows = self.http.get(f"/v2/conversations/{contact_id}", params={"n": limit})["items"]
        return [Message(contact_id, r["body"], datetime.fromtimestamp(r["ts"]), r["direction"] == "in") for r in rows]

    def send(self, contact_id, text):
        return str(self.http.post("/v2/send", json={"to": contact_id, "body": text})["message_id"])


def last_unanswered(provider: MessagingProvider, contact_id: str) -> Message | None:
    """Application logic written once, against the interface, for every provider."""
    msgs = sorted(provider.history(contact_id), key=lambda m: m.sent_at)
    return msgs[-1] if msgs and msgs[-1].from_contact else None
