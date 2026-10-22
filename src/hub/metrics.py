"""Prometheus metrics the hub exposes on /metrics. Names follow `hub_<thing>_<unit>`; label values are bounded
(outcomes, classes, systems), never ids.

The API runs several uvicorn worker processes, so metrics use prometheus_client's multiprocess mode when
PROMETHEUS_MULTIPROC_DIR is set (the container sets it and clears the directory on start): without it, each scrape
would see one worker's counters only. Gauges say how they combine across workers.
"""

from __future__ import annotations

import os
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.multiprocess import MultiProcessCollector

SYNC_RUNS = Counter("hub_sync_runs_total", "Sync runs by result", ["status"])
SYNC_DEALS = Counter("hub_sync_deals_total", "Deals processed by the sync, by outcome", ["outcome"])
SYNC_SECONDS = Histogram("hub_sync_run_seconds", "Duration of a sync run", buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300))
# Both "last" timestamps start at the process start, not 0: "an hour without a sync" then means an hour since start
# when there has been none, instead of firing the moment a fresh process is scraped.
SYNC_LAST_SUCCESS = Gauge(
    "hub_sync_last_success_timestamp_seconds",
    "When a sync run last succeeded (or the process started)",
    multiprocess_mode="max",
)
DEAD_LETTERS_OPEN = Gauge(
    "hub_dead_letters_open", "Deals waiting for a person to fix them", multiprocess_mode="mostrecent"
)
RECON_EXCEPTIONS = Gauge(
    "hub_recon_exceptions", "Exceptions found by the last reconciliation run", ["kind"], multiprocess_mode="mostrecent"
)
RECON_LAST_RUN = Gauge(
    "hub_recon_last_run_timestamp_seconds",
    "When reconciliation last ran (or the process started)",
    multiprocess_mode="max",
)
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

_started = time.time()
SYNC_LAST_SUCCESS.set(_started)
RECON_LAST_RUN.set(_started)


def exposition() -> bytes:
    """The text format for /metrics: every worker's samples combined in multiprocess mode, else this process's."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest()


def record_sync(summary: dict[str, object], status: str) -> None:
    SYNC_RUNS.labels(status).inc()
    for outcome in ("created", "linked", "unchanged", "skipped_cancelled", "dead_lettered"):
        value = summary.get(outcome, 0)
        if isinstance(value, int) and value:
            SYNC_DEALS.labels(outcome).inc(value)
    seconds = summary.get("seconds")
    if isinstance(seconds, float | int):
        SYNC_SECONDS.observe(float(seconds))
