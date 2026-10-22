"""Sync throughput against the real Odoo and EspoCRM, by number of workers: each run loads a fresh book (new seed,
so every won deal is new) and invoices it, then a rerun checks nothing was created twice.

    uv run python scripts/measure_sync.py --accounts 120 --workers 1 4 8 --out reports/sync/throughput.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from hub import factory
from hub.config import get_settings
from hub.connectors.espocrm import Espo
from hub.seed.generator import generate
from hub.seed.load import load
from hub.sync.engine import SyncEngine


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", type=int, default=120)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4, 8])
    ap.add_argument("--out", type=Path, default=Path("reports/sync/throughput.json"))
    a = ap.parse_args()
    s = get_settings()
    admin = Espo(s.espocrm_url, admin=("admin", os.environ["ESPOCRM_ADMIN_PASSWORD"]))
    runs = []
    for i, workers in enumerate(a.workers):
        book = generate(accounts=a.accounts, seed=1000 + i)
        load(book, admin, factory.odoo(s, admin=True))
        expected = sum(1 for d in book.deals if d.stage == "Closed Won")
        store = factory.store(s, pool=workers + 1)
        engine = SyncEngine(factory.crm(s), factory.erp(s), store, workers=workers)
        t0 = time.perf_counter()
        stats = engine.run()
        seconds = time.perf_counter() - t0
        rerun = SyncEngine(factory.crm(s), factory.erp(s), store, workers=workers).run(full=True)
        summary = stats.summary()
        runs.append(
            {
                "workers": workers,
                "book_seed": 1000 + i,
                "won_deals_in_book": expected,
                "seen": summary["seen"],
                "created": summary["created"],
                "dead_lettered": summary["dead_lettered"],
                "seconds": round(seconds, 2),
                "invoices_per_second": round(summary["created"] / seconds, 2),
                "deal_ms_p50": summary.get("deal_ms_p50"),
                "deal_ms_p95": summary.get("deal_ms_p95"),
                "rerun_created": rerun.created,
            }
        )
        print(json.dumps(runs[-1]))
        store.close()
    report = {
        "what": "closed-won deals to posted Odoo invoices through the hub, a fresh book per run",
        "setup": "one machine (i5-13600KF, Docker Desktop); Odoo 19 with 4 HTTP workers, EspoCRM 10, Postgres 17",
        "runs": runs,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
