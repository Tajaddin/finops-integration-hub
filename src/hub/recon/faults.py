"""Make both systems disagree on purpose, at known counts, and write down what was done: the ground truth the
reconciliation is scored against. Runs with admin credentials, like the people whose edits it imitates.

Every class gets its own deals (disjoint samples), so one deal never carries two injected faults and the truth is
unambiguous. ERP edits only touch unpaid invoices: Odoo will not reset a paid invoice to draft.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from hub.connectors.espocrm import Espo
from hub.connectors.odoo import OdooErp
from hub.domain import CrmDeal, ErpCustomer, deal_ref
from hub.sync.engine import validate


@dataclass
class Truth:
    items: set[tuple[str, str]] = field(default_factory=set)
    actions: list[dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, key: str, **action: Any) -> None:
        self.items.add((kind, key))
        self.actions.append({"kind": kind, "key": key, **action})

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for kind, _ in self.items:
            out[kind] = out.get(kind, 0) + 1
        return dict(sorted(out.items()))


def inject(
    crm: str,
    deals: list[CrmDeal],
    links: dict[str, dict[str, Any]],
    espo: Espo,
    erp: OdooErp,
    *,
    per_class: int = 8,
    seed: int = 1,
) -> Truth:
    rng = random.Random(seed)  # noqa: S311  (reproducible choice of victims, not security)
    truth = Truth()
    invoices = {i.id: i for i in erp.invoices()}
    linked = [d for d in deals if d.is_won and d.id in links]
    unpaid = [d for d in linked if invoices[links[d.id]["erp_invoice_id"]].payment_state == "not_paid"]
    rng.shuffle(linked)
    rng.shuffle(unpaid)
    used: set[str] = set()

    def take(pool: list[CrmDeal], n: int, when: Any = lambda _d: True) -> list[CrmDeal]:
        picked = [d for d in pool if d.id not in used and when(d)][:n]
        used.update(d.id for d in picked)
        return picked

    for d in take(unpaid, per_class):  # someone re-priced the invoice in the ERP
        inv = invoices[links[d.id]["erp_invoice_id"]]
        line = validate(d)  # it was invoiced, so it passes; this gives back its line
        erp.set_line_price(inv.id, line.unit_price + Decimal("1.00"))
        truth.add("amount_mismatch", d.id, action="ERP line price +1.00", invoice=inv.id)
    for d in take(linked, per_class):  # sales changed the deal after it was invoiced
        new = (d.amount * Decimal("1.10")).quantize(Decimal("0.01"))
        espo.update("Opportunity", d.id, {"amount": float(new)})
        truth.add("amount_mismatch", d.id, action=f"CRM amount {d.amount} -> {new}")
    for d in take(linked, per_class, lambda d: d.currency == "USD"):
        espo.update("Opportunity", d.id, {"amountCurrency": "EUR"})
        truth.add("currency_mismatch", d.id, action="CRM currency USD -> EUR")
    for d in take(unpaid, per_class):
        inv_id = links[d.id]["erp_invoice_id"]
        erp.cancel_invoice(inv_id)
        truth.add("cancelled_invoice", d.id, action="ERP invoice cancelled", invoice=inv_id)
    for d in take(linked, per_class):
        espo.update("Opportunity", d.id, {"stage": "Closed Lost"})
        truth.add(
            "orphan_invoice", str(links[d.id]["erp_invoice_id"]), action="CRM deal moved to Closed Lost", deal=d.id
        )
    for d in take(linked, per_class):  # a second invoice keyed in by hand for the same deal
        customer = ErpCustomer(links[d.id]["erp_customer_id"], "", None, None)
        dup = erp.create_invoice(customer, d, deal_ref(crm, d.id), validate(d))
        erp.post_invoice(dup.id)
        truth.add("duplicate_invoice", d.id, action="second ERP invoice for the deal", invoice=dup.id)
    for d in take(unpaid, per_class):  # paid in the ERP, the CRM not told yet
        inv = invoices[links[d.id]["erp_invoice_id"]]
        erp.register_payment(inv.id, inv.amount_untaxed, inv.invoice_date or date.today())
        truth.add("billing_status_drift", d.id, action="ERP payment registered", invoice=inv.id)

    open_deals = [d for d in deals if d.stage in ("Negotiation", "Proposal") and d.plan and d.seats and d.term]
    rng.shuffle(open_deals)
    for d in take(open_deals, per_class):  # won after the last sync
        espo.update("Opportunity", d.id, {"stage": "Closed Won"})
        truth.add("missing_invoice", d.id, action="CRM deal won after the last sync")

    customers = [c for c in erp.customers() if c.crm_ref and c.website]
    rng.shuffle(customers)
    for c in customers[:per_class]:  # finance keyed the customer in again
        ids = erp.odoo.call(
            "res.partner",
            "create",
            vals_list=[{"name": f"{c.name} (2)", "is_company": True, "website": c.website, "customer_rank": 1}],
            write=True,
        )
        truth.add(
            "duplicate_customer", ",".join(map(str, sorted([c.id, int(ids[0])]))), action="duplicate ERP customer"
        )
    return truth


def score(detected: set[tuple[str, str]], truth: set[tuple[str, str]], kinds: tuple[str, ...]) -> dict[str, Any]:
    per_kind: dict[str, dict[str, Any]] = {}
    for kind in kinds:
        d = {k for k in detected if k[0] == kind}
        t = {k for k in truth if k[0] == kind}
        tp, fp, fn = len(d & t), len(d - t), len(t - d)
        per_kind[kind] = {
            "truth": len(t),
            "detected": len(d),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        }
    tp = sum(v["true_positives"] for v in per_kind.values())
    fp = sum(v["false_positives"] for v in per_kind.values())
    fn = sum(v["false_negatives"] for v in per_kind.values())
    return {
        "overall": {
            "truth": len(truth),
            "detected": len(detected),
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        },
        "per_class": per_kind,
        "false_positives": sorted(detected - truth),
        "false_negatives": sorted(truth - detected),
    }
