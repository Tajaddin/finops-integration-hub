"""Write observability/grafana/dashboards/finops-hub.json, the dashboard Grafana provisions on start.

Generated rather than exported from the UI so every panel's query is reviewable in one place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "observability" / "grafana" / "dashboards" / "finops-hub.json"
DS = {"type": "prometheus", "uid": "prometheus"}


def panel(
    pid: int,
    title: str,
    kind: str,
    exprs: list[tuple[str, str]],
    x: int,
    y: int,
    w: int = 8,
    h: int = 8,
    unit: str = "short",
) -> dict[str, Any]:
    return {
        "id": pid,
        "title": title,
        "type": kind,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}}
        if kind == "timeseries"
        else {"reduceOptions": {"calcs": ["lastNotNull"]}},
        "targets": [
            {"refId": chr(65 + i), "datasource": DS, "expr": e, "legendFormat": legend}
            for i, (e, legend) in enumerate(exprs)
        ],
    }


def main() -> None:
    panels = [
        panel(1, "Hub up", "stat", [('up{job="hub"}', "up")], 0, 0, 4, 4),
        panel(
            2,
            "Since the last good sync",
            "stat",
            [("time() - hub_sync_last_success_timestamp_seconds", "age")],
            4,
            0,
            5,
            4,
            "s",
        ),
        panel(3, "Deals waiting for a person", "stat", [("hub_dead_letters_open", "dead letters")], 9, 0, 5, 4),
        panel(4, "Open reconciliation exceptions", "stat", [("sum(hub_recon_exceptions)", "exceptions")], 14, 0, 5, 4),
        panel(
            5,
            "Since the last reconciliation",
            "stat",
            [("time() - hub_recon_last_run_timestamp_seconds", "age")],
            19,
            0,
            5,
            4,
            "s",
        ),
        panel(
            6,
            "Deals processed by outcome (per minute)",
            "timeseries",
            [("sum by (outcome) (rate(hub_sync_deals_total[5m])) * 60", "{{outcome}}")],
            0,
            4,
            12,
        ),
        panel(7, "Reconciliation exceptions by class", "timeseries", [("hub_recon_exceptions", "{{kind}}")], 12, 4, 12),
        panel(
            8, "Tickets", "timeseries", [("sum by (action) (increase(hub_tickets_total[1h]))", "{{action}}")], 0, 12, 8
        ),
        panel(
            9,
            "CRM webhook deliveries",
            "timeseries",
            [("sum by (result) (rate(hub_webhook_deliveries_total[5m])) * 60", "{{result}}")],
            8,
            12,
            8,
        ),
        panel(
            10,
            "Triaged messages",
            "timeseries",
            [("sum by (queue, decision) (increase(hub_triage_messages_total[1h]))", "{{queue}} / {{decision}}")],
            16,
            12,
            8,
        ),
        panel(
            11,
            "Triage latency",
            "timeseries",
            [
                ("histogram_quantile(0.5, sum by (le) (rate(hub_triage_seconds_bucket[10m])))", "p50"),
                ("histogram_quantile(0.95, sum by (le) (rate(hub_triage_seconds_bucket[10m])))", "p95"),
            ],
            0,
            20,
            8,
            unit="s",
        ),
        panel(
            12,
            "API p95 by route",
            "timeseries",
            [("histogram_quantile(0.95, sum by (le, route) (rate(hub_http_request_seconds_bucket[5m])))", "{{route}}")],
            8,
            20,
            8,
            unit="s",
        ),
        panel(
            13,
            "API error share",
            "timeseries",
            [
                (
                    'sum(rate(hub_http_request_seconds_count{status=~"5.."}[5m])) / sum(rate(hub_http_request_seconds_count[5m]))',
                    "5xx share",
                )
            ],
            16,
            20,
            8,
            unit="percentunit",
        ),
    ]
    dashboard = {
        "uid": "finops-hub",
        "title": "Finance ops integration hub",
        "tags": ["finops"],
        "timezone": "utc",
        "schemaVersion": 39,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "panels": panels,
    }
    OUT.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(OUT.parents[3])}: {len(panels)} panels")


if __name__ == "__main__":
    main()
