"""Reconciliation: prove the CRM and the ERP agree, or list every disagreement with its class.

Input is a full read of both sides (deals, invoices, customers); output is a list of exceptions, each with a stable
fingerprint so the ticket router can file it once and close it when it goes away. Pure functions: no I/O here.

Classes, and who fixes them:

| class | meaning | queue |
|---|---|---|
| missing_invoice | won deal, no invoice in the ERP | Finance Ops |
| cancelled_invoice | won deal, every invoice for it cancelled | Finance Ops |
| duplicate_invoice | more than one live invoice for one deal | Billing |
| amount_mismatch | invoice untaxed amount differs from the deal amount | Billing |
| currency_mismatch | invoice currency differs from the deal currency | Billing |
| orphan_invoice | live invoice for a deal that is missing or no longer won | Billing |
| duplicate_customer | two ERP customers with the same website domain | Finance Ops |
| billing_status_drift | the CRM shows a different billing status than the ERP | Collections |
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from hub.domain import CrmDeal, ErpCustomer, ErpInvoice, deal_ref, normalise_domain

QUEUE = {
    "missing_invoice": "Finance Ops",
    "cancelled_invoice": "Finance Ops",
    "duplicate_invoice": "Billing",
    "amount_mismatch": "Billing",
    "currency_mismatch": "Billing",
    "orphan_invoice": "Billing",
    "duplicate_customer": "Finance Ops",
    "billing_status_drift": "Collections",
}
SEVERITY = {
    "missing_invoice": "high",
    "cancelled_invoice": "normal",
    "duplicate_invoice": "high",
    "amount_mismatch": "high",
    "currency_mismatch": "high",
    "orphan_invoice": "high",
    "duplicate_customer": "normal",
    "billing_status_drift": "low",
}
KINDS = tuple(QUEUE)


@dataclass(frozen=True)
class ReconException:
    kind: str
    key: str  # the deal id, the invoice id or the sorted customer ids the exception is about
    summary: str
    detail: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.kind}:{self.key}".encode()).hexdigest()[:16]

    @property
    def queue(self) -> str:
        return QUEUE[self.kind]

    @property
    def severity(self) -> str:
        return SEVERITY[self.kind]


def reconcile(
    crm: str,
    deals: Iterable[CrmDeal],
    invoices: Iterable[ErpInvoice],
    customers: Iterable[ErpCustomer],
    dead_letters: dict[str, str] | None = None,
) -> list[ReconException]:
    prefix = f"{crm}:"
    by_origin: dict[str, list[ErpInvoice]] = defaultdict(list)
    for inv in invoices:
        if inv.origin and inv.origin.startswith(prefix):
            by_origin[inv.origin].append(inv)
    deal_by_id = {d.id: d for d in deals}
    out: list[ReconException] = []
    for deal in deal_by_id.values():
        if deal.is_won:
            out += _won_deal(
                deal, sorted(by_origin.get(deal_ref(crm, deal.id), []), key=lambda i: i.id), dead_letters or {}
            )
    out += _orphans(by_origin, deal_by_id, prefix)
    out += _duplicate_customers(customers)
    return sorted(out, key=lambda e: (KINDS.index(e.kind), e.key))


def _won_deal(deal: CrmDeal, found: list[ErpInvoice], dead_letters: dict[str, str]) -> list[ReconException]:
    live = [i for i in found if i.state != "cancel"]
    if not found:
        why = dead_letters.get(deal.id)
        return [
            ReconException(
                "missing_invoice",
                deal.id,
                f"Won deal {deal.name!r} ({deal.amount} {deal.currency}) has no invoice"
                + (f": the sync could not invoice it ({why})" if why else ""),
                {"deal": deal.id, "amount": str(deal.amount), "currency": deal.currency, "dead_letter": why},
            )
        ]
    if not live:
        numbers = ", ".join(str(i.number or i.id) for i in found)
        return [
            ReconException(
                "cancelled_invoice",
                deal.id,
                f"Won deal {deal.name!r}: every invoice for it is cancelled ({numbers})",
                {"deal": deal.id, "invoices": [i.id for i in found]},
            )
        ]
    out: list[ReconException] = []
    if len(live) > 1:
        out.append(
            ReconException(
                "duplicate_invoice",
                deal.id,
                f"Deal {deal.name!r} has {len(live)} live invoices: {', '.join(str(i.number or i.id) for i in live)}",
                {"deal": deal.id, "invoices": [i.id for i in live]},
            )
        )
    inv = live[0]
    if inv.currency != deal.currency:
        out.append(
            ReconException(
                "currency_mismatch",
                deal.id,
                f"Deal {deal.name!r} is in {deal.currency}, invoice {inv.number} is in {inv.currency}",
                {"deal": deal.id, "invoice": inv.id, "crm": deal.currency, "erp": inv.currency},
            )
        )
    elif inv.amount_untaxed != deal.amount:
        out.append(
            ReconException(
                "amount_mismatch",
                deal.id,
                f"Deal {deal.name!r} is {deal.amount} {deal.currency}, invoice {inv.number} is {inv.amount_untaxed}"
                f" (difference {inv.amount_untaxed - deal.amount})",
                {"deal": deal.id, "invoice": inv.id, "crm": str(deal.amount), "erp": str(inv.amount_untaxed)},
            )
        )
    if deal.billing_status is not None and deal.billing_status != inv.billing_status:
        out.append(
            ReconException(
                "billing_status_drift",
                deal.id,
                f"Deal {deal.name!r} shows {deal.billing_status.value} in the CRM, "
                f"invoice {inv.number} is {inv.billing_status.value}",
                {"deal": deal.id, "invoice": inv.id, "crm": deal.billing_status.value, "erp": inv.billing_status.value},
            )
        )
    return out


def _orphans(by_origin: dict[str, list[ErpInvoice]], deals: dict[str, CrmDeal], prefix: str) -> list[ReconException]:
    out: list[ReconException] = []
    for origin, invs in by_origin.items():
        owner = deals.get(origin[len(prefix) :])
        if owner is not None and owner.is_won:
            continue
        state = "does not exist" if owner is None else f"is {owner.stage!r}"
        for inv in invs:
            if inv.state == "cancel":
                continue
            out.append(
                ReconException(
                    "orphan_invoice",
                    str(inv.id),
                    f"Invoice {inv.number or inv.id} ({inv.amount_untaxed} {inv.currency}) is for a deal that {state}",
                    {"invoice": inv.id, "origin": origin, "deal_stage": owner.stage if owner else None},
                )
            )
    return out


def _duplicate_customers(customers: Iterable[ErpCustomer]) -> list[ReconException]:
    by_domain: dict[str, list[ErpCustomer]] = defaultdict(list)
    for c in customers:
        if (domain := normalise_domain(c.website)) is not None:
            by_domain[domain].append(c)
    out: list[ReconException] = []
    for domain, group in by_domain.items():
        if len(group) > 1:
            ids = sorted(c.id for c in group)
            out.append(
                ReconException(
                    "duplicate_customer",
                    ",".join(map(str, ids)),
                    f"{len(group)} ERP customers share the website {domain}: {', '.join(c.name for c in group)}",
                    {"domain": domain, "customers": ids},
                )
            )
    return out
