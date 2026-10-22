"""Ticket systems: one interface, three adapters (Zammad self-hosted, GitHub Issues, Jira Cloud)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Ticket:
    id: str
    ref: str  # what a person sees: Zammad's ticket number, GitHub's #n, Jira's FIN-12
    url: str | None = None


class TicketSink(Protocol):
    name: str

    def open(self, title: str, body: str, *, queue: str, priority: str, tags: list[str]) -> Ticket: ...

    def close(self, ticket_id: str, note: str) -> None: ...

    def is_open(self, ticket_id: str) -> bool:
        """False when a person closed it in the ticket system (the router then files a new one if still detected)."""
        ...
