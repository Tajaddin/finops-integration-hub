"""Prometheus metrics the hub exposes on /metrics. Names follow `hub_<thing>_<unit>`; label values are bounded
(outcomes, classes, systems), never ids."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

SYNC_RUNS = Counter("hub_sync_runs_total", "Sync runs by result", ["status"])
SYNC_DEALS = Counter("hub_sync_deals_total", "Deals processed by the sync, by outcome", ["outcome"])
SYNC_SECONDS = Histogram("hub_sync_run_seconds", "Duration of a sync run", buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300))
SYNC_LAST_SUCCESS = Gauge("hub_sync_last_success_timestamp_seconds", "When a sync run last succeeded")
DEAD_LETTERS_OPEN = Gauge("hub_dead_letters_open", "Deals waiting for a person to fix them")
RECON_EXCEPTIONS = Gauge("hub_recon_exceptions", "Exceptions found by the last reconciliation run", ["kind"])
RECON_LAST_RUN = Gauge("hub_recon_last_run_timestamp_seconds", "When reconciliation last ran")
TICKETS = Counter("hub_tickets_total", "Ticket actions", ["system", "action"])
WEBHOOKS = Counter("hub_webhook_deliveries_total", "CRM webhook deliveries by result", ["source", "result"])
TRIAGE = Counter("hub_triage_messages_total", "Messages triaged, by queue and decision", ["queue", "decision"])
TRIAGE_SECONDS = Histogram("hub_triage_seconds", "Time to triage one message", buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32))
HTTP_SECONDS = Histogram(
    "hub_http_request_seconds",
    "Hub API latency",
    ["route", "method", "status"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


def record_sync(summary: dict[str, object], status: str) -> None:
    SYNC_RUNS.labels(status).inc()
    for outcome in ("created", "linked", "unchanged", "skipped_cancelled", "dead_lettered"):
        value = summary.get(outcome, 0)
        if isinstance(value, int) and value:
            SYNC_DEALS.labels(outcome).inc(value)
    seconds = summary.get("seconds")
    if isinstance(seconds, float | int):
        SYNC_SECONDS.observe(float(seconds))
