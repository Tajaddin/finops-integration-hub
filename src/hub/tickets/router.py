"""Exceptions in, tickets out: one ticket per exception fingerprint while it is detected, closed with a note when a
later run no longer finds it. If a person closes a ticket while the problem is still there, the next run files a
fresh one that says so, instead of re-opening theirs."""

from __future__ import annotations

from dataclasses import dataclass

from hub import log
from hub.connectors.http import ApiError
from hub.recon.rules import ReconException
from hub.store import Store
from hub.tickets import TicketSink

logger = log.get(__name__)


@dataclass
class RouteStats:
    opened: int = 0
    already_open: int = 0
    reopened_after_manual_close: int = 0
    closed_resolved: int = 0
    failed: int = 0


def body_for(exc: ReconException, run_id: int) -> str:
    lines = [exc.summary, "", f"Class: {exc.kind} ({exc.severity})", f"Found by reconciliation run {run_id}."]
    for k, v in exc.detail.items():
        if v not in (None, "", []):
            lines.append(f"{k}: {v}")
    lines += ["", f"Fingerprint {exc.fingerprint}: the hub closes this ticket when a run no longer finds the problem."]
    return "\n".join(lines)


def route(exceptions: list[ReconException], sink: TicketSink, store: Store, run_id: int) -> RouteStats:
    stats = RouteStats()
    open_links = store.open_ticket_links(sink.name)
    current = {e.fingerprint: e for e in exceptions}
    for fp, exc in current.items():
        link = open_links.get(fp)
        try:
            if link is not None:
                if sink.is_open(link["ticket_id"]):
                    store.touch_ticket(sink.name, fp, run_id)
                    stats.already_open += 1
                    continue
                store.close_ticket_link(sink.name, fp)  # closed by a person, problem still there
                stats.reopened_after_manual_close += 1
            title = f"[{exc.kind.replace('_', ' ')}] {exc.summary}"[:250]
            body = body_for(exc, run_id)
            if link is not None:
                body += f"\n\nA previous ticket ({link['ticket_ref']}) was closed while the problem was still detected."
            ticket = sink.open(
                title, body, queue=exc.queue, priority=exc.severity, tags=["reconciliation", exc.kind, f"fp-{fp}"]
            )
            store.save_ticket_link(sink.name, fp, exc.kind, exc.key, ticket.id, ticket.ref, run_id)
            stats.opened += 1
        except ApiError as e:
            stats.failed += 1
            logger.warning("ticket_failed", fingerprint=fp, error=str(e))
    for fp, link in open_links.items():
        if fp in current:
            continue
        try:
            sink.close(link["ticket_id"], f"Resolved: reconciliation run {run_id} no longer finds this problem.")
            store.close_ticket_link(sink.name, fp)
            stats.closed_resolved += 1
        except ApiError as e:
            stats.failed += 1
            logger.warning("ticket_close_failed", fingerprint=fp, error=str(e))
    logger.info("tickets_routed", system=sink.name, run=run_id, **stats.__dict__)
    return stats
