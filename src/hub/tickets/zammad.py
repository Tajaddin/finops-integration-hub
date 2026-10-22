"""Zammad REST API (`/api/v1`), as the agent the bootstrap created (`Authorization: Token token=...`)."""

from __future__ import annotations

import httpx

from hub.connectors.http import Http
from hub.tickets import Ticket

PRIORITY = {"low": 1, "normal": 2, "high": 3}
CLOSED = {"closed", "merged", "removed"}


class ZammadTickets:
    name = "zammad"

    def __init__(
        self,
        url: str,
        token: str,
        *,
        customer: str = "reconciliation@finops.local",
        timeout: float = 20.0,
        retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.customer = customer
        headers = {"Authorization": f"Token token={token}", "Content-Type": "application/json"}
        self.http = Http("zammad", self.url + "/api/v1", headers, timeout, retries, transport=transport)

    def open(
        self, title: str, body: str, *, queue: str, priority: str, tags: list[str], customer: str | None = None
    ) -> Ticket:
        data = self.http.request(
            "POST",
            "/tickets",
            json={
                "title": title[:250],
                "group": queue,
                "customer_id": f"guess:{customer or self.customer}",
                "priority_id": PRIORITY[priority],
                "tags": ",".join(tags),
                "article": {
                    "subject": title[:250],
                    "body": body,
                    "type": "note",
                    "internal": False,
                    "content_type": "text/plain",
                },
            },
            idempotent=False,
        ).json()
        return Ticket(str(data["id"]), str(data["number"]), f"{self.url}/#ticket/zoom/{data['id']}")

    def note(self, ticket_id: str, body: str, internal: bool = True) -> None:
        self.http.request(
            "POST",
            "/ticket_articles",
            json={
                "ticket_id": int(ticket_id),
                "body": body,
                "type": "note",
                "internal": internal,
                "content_type": "text/plain",
            },
            idempotent=False,
        )

    def close(self, ticket_id: str, note: str) -> None:
        self.note(ticket_id, note)
        self.http.request("PUT", f"/tickets/{int(ticket_id)}", json={"state": "closed"})

    def is_open(self, ticket_id: str) -> bool:
        data = self.http.request("GET", f"/tickets/{int(ticket_id)}", params={"expand": "true"}).json()
        return str(data.get("state", "")).lower() not in CLOSED
