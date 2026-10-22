"""GitHub Issues as a ticket system (REST API v3, a fine-grained token with Issues read/write on one repository)."""

from __future__ import annotations

import httpx

from hub.connectors.http import Http
from hub.tickets import Ticket


class GitHubTickets:
    name = "github"

    def __init__(
        self,
        repo: str,
        token: str,
        *,
        timeout: float = 20.0,
        retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        owner, _, name = repo.partition("/")
        if not owner or not name or "/" in name:
            raise ValueError(f"expected owner/name, got {repo!r}")
        self.repo = repo
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.http = Http("github", "https://api.github.com", headers, timeout, retries, transport=transport)

    def open(self, title: str, body: str, *, queue: str, priority: str, tags: list[str]) -> Ticket:
        labels = [f"queue:{queue.lower().replace(' ', '-')}", f"priority:{priority}", *tags]
        data = self.http.request(
            "POST",
            f"/repos/{self.repo}/issues",
            json={"title": title[:250], "body": body, "labels": labels},
            idempotent=False,
        ).json()
        return Ticket(str(data["number"]), f"#{data['number']}", data.get("html_url"))

    def close(self, ticket_id: str, note: str) -> None:
        self.http.request(
            "POST", f"/repos/{self.repo}/issues/{int(ticket_id)}/comments", json={"body": note}, idempotent=False
        )
        self.http.request(
            "PATCH",
            f"/repos/{self.repo}/issues/{int(ticket_id)}",
            json={"state": "closed", "state_reason": "completed"},
        )

    def is_open(self, ticket_id: str) -> bool:
        data = self.http.request("GET", f"/repos/{self.repo}/issues/{int(ticket_id)}").json()
        return bool(data["state"] == "open")
