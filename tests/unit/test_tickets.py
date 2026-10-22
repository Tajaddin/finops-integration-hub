"""The router's lifecycle (open once, leave alone, close when resolved, refile after a manual close) against a real
Postgres, and the three adapters' requests against the vendors' documented shapes."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from hub.recon.rules import ReconException
from hub.store import Store
from hub.tickets import Ticket
from hub.tickets.github import GitHubTickets
from hub.tickets.jira import JiraTickets, adf
from hub.tickets.router import route
from hub.tickets.zammad import ZammadTickets


class FakeSink:
    name = "fake"

    def __init__(self) -> None:
        self.tickets: dict[str, dict[str, Any]] = {}

    def open(
        self, title: str, body: str, *, queue: str, priority: str, tags: list[str], customer: str | None = None
    ) -> Ticket:
        tid = str(len(self.tickets) + 1)
        self.tickets[tid] = {
            "title": title,
            "body": body,
            "queue": queue,
            "priority": priority,
            "tags": tags,
            "open": True,
            "notes": [],
        }
        return Ticket(tid, f"#{tid}")

    def note(self, ticket_id: str, body: str) -> None:
        self.tickets[ticket_id]["notes"].append(body)

    def close(self, ticket_id: str, note: str) -> None:
        self.tickets[ticket_id]["open"] = False
        self.tickets[ticket_id]["notes"].append(note)

    def is_open(self, ticket_id: str) -> bool:
        return bool(self.tickets[ticket_id]["open"])


A = ReconException("amount_mismatch", "deal1", "Deal is 100 USD, invoice is 101", {"crm": "100", "erp": "101"})
B = ReconException("orphan_invoice", "77", "Invoice 77 is for a deal that is 'Closed Lost'")


def test_ticket_lifecycle(store: Store) -> None:
    sink = FakeSink()
    first = route([A, B], sink, store, run_id=1)
    assert (first.opened, first.already_open) == (2, 0)
    t = sink.tickets["1"]
    assert t["queue"] == "Billing" and t["priority"] == "high" and f"fp-{A.fingerprint}" in t["tags"]
    assert "crm: 100" in t["body"] and A.fingerprint in t["body"]

    again = route([A, B], sink, store, run_id=2)
    assert (again.opened, again.already_open) == (0, 2), "a rerun must not file duplicates"

    resolved = route([A], sink, store, run_id=3)
    assert resolved.closed_resolved == 1
    assert sink.tickets["2"]["open"] is False and "run 3" in sink.tickets["2"]["notes"][0]

    sink.tickets["1"]["open"] = False  # a person closed it, the problem is still there
    refiled = route([A], sink, store, run_id=4)
    assert (refiled.reopened_after_manual_close, refiled.opened) == (1, 1)
    assert "#1" in sink.tickets["3"]["body"]


def recorder(responses: dict[tuple[str, str], Any]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for (method, suffix), body in responses.items():
            if request.method == method and request.url.path.endswith(suffix):
                return httpx.Response(204 if body is None else 200, json=body)
        return httpx.Response(404, json={"message": "no stub"})

    return httpx.MockTransport(handler), seen


def test_zammad_creates_a_note_ticket_for_the_reconciliation_customer() -> None:
    transport, seen = recorder(
        {
            ("POST", "/tickets"): {"id": 12, "number": "31012"},
            ("POST", "/ticket_articles"): {"id": 1},
            ("PUT", "/tickets/12"): {"id": 12},
        }
    )
    z = ZammadTickets("http://zammad:8080", "tok", transport=transport)
    t = z.open("[amount mismatch] x", "body", queue="Billing", priority="high", tags=["reconciliation", "fp-abc"])
    body = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Token token=tok"
    assert body["group"] == "Billing" and body["priority_id"] == 3 and body["tags"] == "reconciliation,fp-abc"
    assert body["customer_id"] == "guess:reconciliation@finops.local" and body["article"]["type"] == "note"
    assert t == Ticket("12", "31012", "http://zammad:8080/#ticket/zoom/12")
    z.close("12", "Resolved")
    assert json.loads(seen[-1].content) == {"state": "closed"} and json.loads(seen[-2].content)["internal"] is True


def test_github_issue_with_queue_and_priority_labels_then_closed_as_completed() -> None:
    transport, seen = recorder(
        {
            ("POST", "/issues"): {"number": 5, "html_url": "https://github.com/o/r/issues/5"},
            ("POST", "/issues/5/comments"): {"id": 1},
            ("PATCH", "/issues/5"): {"number": 5},
            ("GET", "/issues/5"): {"state": "closed"},
        }
    )
    g = GitHubTickets("o/r", "ghp_test", transport=transport)
    t = g.open("title", "body", queue="Finance Ops", priority="normal", tags=["reconciliation"])
    assert json.loads(seen[0].content)["labels"] == ["queue:finance-ops", "priority:normal", "reconciliation"]
    assert seen[0].headers["x-github-api-version"] == "2022-11-28" and t.ref == "#5"
    g.close("5", "done")
    assert json.loads(seen[-1].content) == {"state": "closed", "state_reason": "completed"}
    assert g.is_open("5") is False
    with pytest.raises(ValueError, match="owner/name"):
        GitHubTickets("not-a-repo", "t")


def test_jira_issue_in_adf_and_closed_through_the_done_transition() -> None:
    transport, seen = recorder(
        {
            ("POST", "/rest/api/3/issue"): {"id": "10001", "key": "FIN-7"},
            ("POST", "/rest/api/3/issue/FIN-7/comment"): {"id": "1"},
            ("GET", "/rest/api/3/issue/FIN-7/transitions"): {
                "transitions": [
                    {"id": "11", "name": "To Do", "to": {"statusCategory": {"key": "new"}}},
                    {"id": "31", "name": "Done", "to": {"statusCategory": {"key": "done"}}},
                ]
            },
            ("POST", "/rest/api/3/issue/FIN-7/transitions"): None,
        }
    )
    j = JiraTickets("https://acme.atlassian.net", "me@acme.com", "tok", "FIN", transport=transport)
    t = j.open("title", "line one\n\nline two", queue="Billing", priority="high", tags=["reconciliation"])
    fields = json.loads(seen[0].content)["fields"]
    assert fields["project"] == {"key": "FIN"} and fields["priority"] == {"name": "High"}
    assert fields["description"] == adf("line one\n\nline two") and len(fields["description"]["content"]) == 2
    assert seen[0].headers["authorization"] == "Basic " + base64.b64encode(b"me@acme.com:tok").decode()
    assert t.ref == "FIN-7" and t.url == "https://acme.atlassian.net/browse/FIN-7"
    j.close("FIN-7", "Resolved")
    assert json.loads(seen[-1].content) == {"transition": {"id": "31"}}
