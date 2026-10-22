"""Jira Cloud REST API v3 (basic auth with an account e-mail and an API token). Descriptions and comments are
Atlassian Document Format; closing is a workflow transition into the Done status category."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from hub.connectors.http import ApiError, Http
from hub.tickets import Ticket

PRIORITY = {"low": "Low", "normal": "Medium", "high": "High"}


def adf(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": para}]} for para in text.split("\n\n") if para
        ],
    }


class JiraTickets:
    name = "jira"

    def __init__(
        self,
        url: str,
        email: str,
        token: str,
        project: str,
        *,
        timeout: float = 20.0,
        retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.project = project
        basic = base64.b64encode(f"{email}:{token}".encode()).decode()
        headers = {"Authorization": f"Basic {basic}", "Accept": "application/json", "Content-Type": "application/json"}
        self.http = Http("jira", self.url, headers, timeout, retries, transport=transport)

    def open(
        self, title: str, body: str, *, queue: str, priority: str, tags: list[str], customer: str | None = None
    ) -> Ticket:
        fields = {
            "project": {"key": self.project},
            "issuetype": {"name": "Task"},
            "summary": title[:250],
            "description": adf(body),
            "priority": {"name": PRIORITY[priority]},
            "labels": [t.replace(" ", "-") for t in [f"queue-{queue}", *tags]],
        }
        data = self.http.request("POST", "/rest/api/3/issue", json={"fields": fields}, idempotent=False).json()
        return Ticket(data["key"], data["key"], f"{self.url}/browse/{data['key']}")

    def note(self, ticket_id: str, body: str) -> None:
        self.http.request("POST", f"/rest/api/3/issue/{ticket_id}/comment", json={"body": adf(body)}, idempotent=False)

    def close(self, ticket_id: str, note: str) -> None:
        self.http.request("POST", f"/rest/api/3/issue/{ticket_id}/comment", json={"body": adf(note)}, idempotent=False)
        transitions = self.http.request("GET", f"/rest/api/3/issue/{ticket_id}/transitions").json()["transitions"]
        done = next((t for t in transitions if t.get("to", {}).get("statusCategory", {}).get("key") == "done"), None)
        if done is None:
            raise ApiError("jira", None, f"no transition into Done from the current status of {ticket_id}")
        self.http.request(
            "POST",
            f"/rest/api/3/issue/{ticket_id}/transitions",
            json={"transition": {"id": done["id"]}},
            idempotent=True,
        )

    def is_open(self, ticket_id: str) -> bool:
        data = self.http.request("GET", f"/rest/api/3/issue/{ticket_id}", params={"fields": "status"}).json()
        return bool(data["fields"]["status"]["statusCategory"]["key"] != "done")
