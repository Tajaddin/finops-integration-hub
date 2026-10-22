"""The measured reconciliation scenario behind reports/recon/: start from a synced book, let customers pay, align the
CRM, inject faults at known counts, then check that reconciliation finds them, that tickets are filed once, and that
they close themselves once the systems agree again.

    hub seed && hub sync && hub recon-eval     (each step is also its own CLI command)
"""

from __future__ import annotations

import random
import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from hub import log
from hub.connectors.base import CrmSource
from hub.connectors.espocrm import Espo
from hub.connectors.odoo import OdooErp
from hub.recon.faults import inject, score
from hub.recon.rules import KINDS
from hub.recon.run import run_reconciliation
from hub.store import Store
from hub.sync.engine import SyncEngine
from hub.tickets import TicketSink

logger = log.get(__name__)


def pay_some(erp_admin: OdooErp, share: float = 0.7, partial_share: float = 0.08, seed: int = 5) -> dict[str, int]:
    """Customers pay most posted invoices: in full, or about half for a few."""
    rng = random.Random(seed)  # noqa: S311
    unpaid = sorted(
        (i for i in erp_admin.invoices() if i.state == "posted" and i.payment_state == "not_paid"), key=lambda i: i.id
    )
    full = partial = 0
    for inv in unpaid:
        roll = rng.random()
        if roll >= share + partial_share:
            continue
        when = min(date.today(), (inv.invoice_date or date.today()) + timedelta(days=rng.randint(3, 45)))
        if roll < share:
            erp_admin.register_payment(inv.id, inv.amount_untaxed, when)
            full += 1
        else:
            erp_admin.register_payment(inv.id, (inv.amount_untaxed / 2).quantize(Decimal("0.01")), when)
            partial += 1
    return {"unpaid_before": len(unpaid), "paid_in_full": full, "paid_partially": partial}


def evaluate(
    crm: CrmSource,
    erp: OdooErp,
    erp_admin: OdooErp,
    espo_admin: Espo,
    store: Store,
    sink: TicketSink | None,
    *,
    per_class: int = 8,
    seed: int = 1,
    natural: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """`natural`: the exceptions this book carries by design (deals with data problems); checked against the
    measured baseline, which is what joins the injected faults in the ground truth."""
    report: dict[str, Any] = {"per_class_injected": per_class, "seed": seed}
    engine = SyncEngine(crm, erp, store)

    report["payments"] = pay_some(erp_admin)
    report["status_refresh_before"] = engine.refresh_billing_status()
    baseline, base_exc = run_reconciliation(crm, erp, store, None)
    report["baseline"] = {"exceptions": baseline["exceptions"], "by_kind": baseline["by_kind"]}
    # Whatever is wrong before any fault is injected is part of the truth: on a fresh book that is exactly the deals
    # with data problems; on a shared stack it also includes other books' (the CI integration test leaves some).
    before = {(e.kind, e.key) for e in base_exc}
    if natural is not None:
        report["baseline"]["book_data_problems_all_found"] = natural <= before
        report["baseline"]["exceptions_not_from_this_book"] = len(before - natural)
    natural = before

    deals = list(crm.deals())
    truth = inject(crm.name, deals, store.links(crm.name), espo_admin, erp_admin, per_class=per_class, seed=seed)
    truth.items |= natural
    report["injected"] = truth.counts()

    t0 = time.perf_counter()
    stats, exceptions = run_reconciliation(crm, erp, store, sink)
    report["detect_run"] = {
        k: stats[k]
        for k in ("deals", "won_deals", "invoices", "customers", "exceptions", "read_seconds", "compare_seconds")
    }
    report["detect_run"]["seconds"] = round(time.perf_counter() - t0, 3)
    report["tickets_first_run"] = stats.get("tickets")
    report["score"] = score({(e.kind, e.key) for e in exceptions}, truth.items, KINDS)

    again, _ = run_reconciliation(crm, erp, store, sink)
    report["tickets_rerun"] = again.get("tickets")

    # repair what automation can: billing status back to the CRM, invoices for deals won since the last sync
    report["repair"] = {"status_refresh": engine.refresh_billing_status(), "sync": engine.run().summary()}
    after, _ = run_reconciliation(crm, erp, store, sink)
    report["after_repair"] = {
        "exceptions": after["exceptions"],
        "by_kind": after["by_kind"],
        "tickets": after.get("tickets"),
    }
    report["truth_actions"] = truth.actions
    logger.info(
        "recon_eval_finished",
        precision=report["score"]["overall"]["precision"],
        recall=report["score"]["overall"]["recall"],
    )
    return report
