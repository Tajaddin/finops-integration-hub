"""Measure won-deal-to-posted-invoice latency through the webhook path on the running stack.

    uv run python scripts/measure_webhook.py --samples 5 --out reports/sync/webhook.json

Each sample moves one open deal (with valid plan, seats and term) to Closed Won in EspoCRM as the admin, then
polls Odoo until a posted invoice with that deal as its source document exists. EspoCRM delivers webhooks from a
scheduled job that runs every minute, so the latency includes the wait for the next job run.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

from hub import factory
from hub.config import get_settings
from hub.connectors.espocrm import Espo
from hub.domain import deal_ref


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=150)
    ap.add_argument("--out", type=Path, default=Path("reports/sync/webhook.json"))
    a = ap.parse_args()
    s = get_settings()
    admin = Espo(s.espocrm_url, admin=("admin", os.environ["ESPOCRM_ADMIN_PASSWORD"]))
    erp = factory.erp(s)
    rows = admin.list_page(
        "Opportunity",
        [{"type": "in", "attribute": "stage", "value": ["Qualification", "Proposal", "Negotiation"]}],
        ["id", "cPlan", "cSeats", "cTerm"],
    )["list"]
    candidates = [r for r in rows if r.get("cPlan") and r.get("cSeats") and r.get("cTerm")]
    samples = []
    for row in candidates[: a.samples]:
        t0 = time.time()
        admin.update("Opportunity", row["id"], {"stage": "Closed Won"})
        latency = None
        while time.time() - t0 < a.timeout:
            if any(i.state == "posted" for i in erp.find_invoices(deal_ref("espocrm", row["id"]))):
                latency = round(time.time() - t0, 1)
                break
            time.sleep(1)
        samples.append({"deal": row["id"], "seconds": latency})
        print(json.dumps(samples[-1]))
    ok = [x["seconds"] for x in samples if x["seconds"] is not None]
    report = {
        "what": "deal moved to Closed Won in EspoCRM -> signed webhook -> hub -> posted Odoo invoice",
        "setup": "local Docker stack on one machine; EspoCRM's ProcessWebhookQueue job runs every minute",
        "samples": samples,
        "delivered": len(ok),
        "seconds_median": statistics.median(ok) if ok else None,
        "seconds_max": max(ok) if ok else None,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: report[k] for k in ("delivered", "seconds_median", "seconds_max")}))
    return 0 if len(ok) == len(samples) else 1


if __name__ == "__main__":
    raise SystemExit(main())
