"""Closed-won deals in the CRM become posted invoices in the ERP, exactly once.

Exactly once rests on one rule: before creating anything, look in the ERP for an invoice whose source document is
this deal (`crm:<deal id>`). The ERP is the source of truth, so it does not matter where a previous run stopped:

- crashed after the invoice was created but before the hub recorded it: the next run finds it and links it;
- crashed after creating a draft but before posting: the next run finds the draft and posts it;
- the create call timed out after reaching Odoo (AmbiguousWrite): look again before deciding anything.

Runs are serialised per CRM by a Postgres advisory lock, so two runs cannot both miss and both create. A deal that
cannot be invoiced until a person fixes it (unknown plan, price that disagrees with the price book) goes to the
dead-letter table with the reason, and the run carries on. CRM write-backs go through an outbox (store.py).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from hub import log
from hub.connectors.base import CrmSource, ErpSink, PermanentError
from hub.connectors.http import AmbiguousWrite, ApiError
from hub.domain import BillingStatus, CrmAccount, CrmDeal, ErpCustomer, ErpInvoice, deal_ref, normalise_domain
from hub.pricing import ALLOWED_DEAL_DISCOUNTS, InvoiceLine, invoice_line
from hub.store import Store

logger = log.get(__name__)


@dataclass
class SyncStats:
    seen: int = 0
    created: int = 0
    linked: int = 0
    unchanged: int = 0
    skipped_cancelled: int = 0
    customers_created: int = 0
    dead_lettered: int = 0
    writebacks_sent: int = 0
    writebacks_failed: int = 0
    seconds: float = 0.0
    per_deal_ms: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "per_deal_ms"}
        if self.per_deal_ms:
            ordered = sorted(self.per_deal_ms)
            out["deal_ms_p50"] = round(ordered[len(ordered) // 2], 1)
            out["deal_ms_p95"] = round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1)
        return out


def validate(deal: CrmDeal) -> InvoiceLine:
    """The controls a deal must pass before it can be invoiced; each failure names the field a person must fix.
    Returns the invoice line the price book gives for the deal."""
    plan, seats, term = deal.plan, deal.seats, deal.term
    if plan is None or not seats or term is None:
        missing = [n for n, v in (("plan", plan), ("seats", seats), ("term", term)) if not v]
        raise PermanentError(f"missing {', '.join(missing)}")
    if not deal.account_id:
        raise PermanentError("no account on the deal")
    if deal.discount_pct not in ALLOWED_DEAL_DISCOUNTS:
        raise PermanentError(f"discount {deal.discount_pct}% is not an approved discount")
    try:
        line = invoice_line(plan, seats, term, deal.currency, deal.discount_pct)
    except ValueError as exc:
        raise PermanentError(str(exc)) from exc
    if deal.amount != line.subtotal:
        raise PermanentError(f"amount {deal.amount} {deal.currency} differs from the price book ({line.subtotal})")
    return line


class SyncEngine:
    def __init__(
        self,
        crm: CrmSource,
        erp: ErpSink,
        store: Store,
        *,
        overlap: timedelta = timedelta(minutes=5),
        on_step: Callable[[str, CrmDeal], None] | None = None,
    ) -> None:
        self.crm = crm
        self.erp = erp
        self.store = store
        self.overlap = overlap
        self._accounts: dict[str, CrmAccount] = {}
        self.customers_created = 0
        self._customers: dict[str, ErpCustomer] = {}
        # test hook: called between the steps of one deal, so a test can "crash" the run at any of them
        self._on_step = on_step or (lambda _step, _deal: None)

    def run(self, *, full: bool = False) -> SyncStats:
        stats = SyncStats()
        started = time.perf_counter()
        with self.store.lock(f"sync:{self.crm.name}"):
            run_id = self.store.start_run(self.crm.name)
            status = "failed"
            try:
                cursor = None if full else self.store.cursor(self.crm.name)
                since = cursor - self.overlap if cursor else None
                newest: datetime | None = None
                for deal in self.crm.won_deals(since):
                    stats.seen += 1
                    t0 = time.perf_counter()
                    self._one(deal, stats)
                    stats.per_deal_ms.append((time.perf_counter() - t0) * 1000)
                    newest = deal.modified_at if newest is None else max(newest, deal.modified_at)
                    if stats.seen % 50 == 0 and newest is not None:
                        self.store.set_cursor(self.crm.name, newest)
                if newest is not None:
                    self.store.set_cursor(self.crm.name, newest)
                self.flush_writebacks(stats)
                stats.customers_created = self.customers_created
                status = "succeeded"
            finally:
                stats.seconds = round(time.perf_counter() - started, 3)
                self.store.finish_run(run_id, status, stats.summary())
        logger.info("sync_finished", crm=self.crm.name, **stats.summary())
        return stats

    def sync_deals(self, deal_ids: list[str]) -> SyncStats:
        """Process just these deals (named by a CRM webhook), under the same lock as a full run."""
        stats = SyncStats()
        started = time.perf_counter()
        with self.store.lock(f"sync:{self.crm.name}"):
            for deal_id in dict.fromkeys(deal_ids):
                try:
                    deal = self.crm.deal(deal_id)
                except (PermanentError, ApiError) as exc:
                    logger.warning("webhook_deal_unreadable", deal=deal_id, error=str(exc))
                    continue
                if not deal.is_won:
                    continue
                stats.seen += 1
                self._one(deal, stats)
            self.flush_writebacks(stats)
        stats.customers_created = self.customers_created
        stats.seconds = round(time.perf_counter() - started, 3)
        return stats

    def refresh_billing_status(self) -> dict[str, int]:
        """Write the ERP's billing status (invoiced, partial, paid, overdue) back to every linked won deal whose CRM
        value differs. Cancelled invoices are left to the reconciliation."""
        invoices = {i.id: i for i in self.erp.invoices()}
        links = self.store.links(self.crm.name)
        checked = queued = 0
        for deal in self.crm.won_deals(None):
            link = links.get(deal.id)
            invoice = invoices.get(link["erp_invoice_id"]) if link else None
            if invoice is None or invoice.state == "cancel":
                continue
            checked += 1
            status = invoice.billing_status
            if deal.billing_status != status or deal.erp_invoice_ref != invoice.number:
                self.store.queue_writeback(self.crm.name, deal.id, invoice.number, status.value, force=True)
                queued += 1
        stats = SyncStats()
        self.flush_writebacks(stats)
        return {"checked": checked, "queued": queued, "sent": stats.writebacks_sent, "failed": stats.writebacks_failed}

    def _one(self, deal: CrmDeal, stats: SyncStats) -> None:
        try:
            outcome = self.process(deal)
        except PermanentError as exc:
            self.store.dead_letter(self.crm.name, deal.id, "permanent", str(exc))
            stats.dead_lettered += 1
            logger.warning("deal_dead_lettered", deal=deal.id, reason=str(exc))
            return
        except ApiError as exc:
            if not exc.transient:
                self.store.dead_letter(self.crm.name, deal.id, "permanent", str(exc))
            else:  # retried at the HTTP layer already; park it and let the next run (or a replay) try again
                self.store.dead_letter(self.crm.name, deal.id, "transient", str(exc))
            stats.dead_lettered += 1
            logger.warning("deal_dead_lettered", deal=deal.id, reason=str(exc), status=exc.status)
            return
        setattr(stats, outcome, getattr(stats, outcome) + 1)

    def process(self, deal: CrmDeal) -> str:
        """One deal. Returns created | linked | unchanged | skipped_cancelled."""
        ref = deal_ref(self.crm.name, deal.id)
        existing = self.erp.find_invoices(ref)
        live = [i for i in existing if i.state != "cancel"]
        if live:
            invoice = live[0]
            if invoice.state == "draft":
                self._on_step("before_post", deal)
                invoice = self.erp.post_invoice(invoice.id)
            already = self.store.get_link(self.crm.name, deal.id)
            self._record(deal, invoice)
            return "unchanged" if already and already["erp_invoice_id"] == invoice.id else "linked"
        if existing:  # every invoice for this deal was cancelled in the ERP: a person decided that; do not re-invoice
            return "skipped_cancelled"

        line = validate(deal)
        customer = self._customer(deal)
        self._on_step("before_create", deal)
        try:
            invoice = self.erp.create_invoice(customer, deal, ref, line)
        except AmbiguousWrite:
            found = [i for i in self.erp.find_invoices(ref) if i.state != "cancel"]
            if not found:
                raise
            invoice = found[0]
        self._on_step("before_post", deal)
        invoice = self.erp.post_invoice(invoice.id)
        self._on_step("before_record", deal)
        self._record(deal, invoice)
        return "created"

    def _customer(self, deal: CrmDeal) -> ErpCustomer:
        account_ref = f"{self.crm.name}:{deal.account_id}"
        if account_ref in self._customers:
            return self._customers[account_ref]
        account = self._accounts.get(deal.account_id) or self.crm.account(deal.account_id)
        self._accounts[deal.account_id] = account
        customer = self.erp.find_customer(account_ref, normalise_domain(account.website))
        if customer is None:
            customer = self.erp.create_customer(account, account_ref)
            self.customers_created += 1
        self._customers[account_ref] = customer
        return customer

    def _record(self, deal: CrmDeal, invoice: ErpInvoice) -> None:
        self.store.link(
            self.crm.name,
            deal.id,
            invoice.id,
            invoice.number,
            invoice.partner_id,
            invoice.amount_untaxed,
            invoice.currency,
            invoice.billing_status.value,
        )

    def flush_writebacks(self, stats: SyncStats) -> None:
        for row in self.store.pending_writebacks(self.crm.name):
            try:
                self.crm.write_back(row["deal_id"], row["invoice_ref"], BillingStatus(row["billing_status"]))
            except ApiError as exc:
                self.store.writeback_failed(self.crm.name, row["deal_id"], str(exc))
                stats.writebacks_failed += 1
                continue
            self.store.writeback_done(self.crm.name, row["deal_id"])
            stats.writebacks_sent += 1
