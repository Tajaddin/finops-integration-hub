"""One reconciliation run: read both systems in full, compare, store the exceptions, route them to tickets."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from hub import log
from hub.connectors.base import CrmSource, ErpSink
from hub.recon.rules import KINDS, ReconException, reconcile
from hub.store import Store
from hub.tickets import TicketSink
from hub.tickets.router import route

logger = log.get(__name__)


def run_reconciliation(
    crm: CrmSource, erp: ErpSink, store: Store, sink: TicketSink | None = None
) -> tuple[dict[str, Any], list[ReconException]]:
    t0 = time.perf_counter()
    deals = list(crm.deals())
    invoices = list(erp.invoices())
    customers = list(erp.customers())
    read_s = time.perf_counter() - t0
    dead = {r["deal_id"]: r["detail"] for r in store.open_dead_letters(crm.name)}
    exceptions = reconcile(crm.name, deals, invoices, customers, dead)
    by_kind = Counter(e.kind for e in exceptions)
    stats: dict[str, Any] = {
        "deals": len(deals),
        "won_deals": sum(1 for d in deals if d.is_won),
        "invoices": len(invoices),
        "customers": len(customers),
        "exceptions": len(exceptions),
        "by_kind": {k: by_kind.get(k, 0) for k in KINDS},
        "read_seconds": round(read_s, 3),
        "compare_seconds": round(time.perf_counter() - t0 - read_s, 3),
    }
    run_id = store.save_recon_run(crm.name, exceptions, stats)
    stats["run_id"] = run_id
    if sink is not None:
        stats["tickets"] = route(exceptions, sink, store, run_id).__dict__
    logger.info("recon_finished", **{k: v for k, v in stats.items() if k != "by_kind"})
    return stats, exceptions
