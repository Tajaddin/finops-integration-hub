"""In-memory CRM and ERP that behave like the real adapters where the sync depends on it: the ERP keeps invoices by
source document, can be told to fail a create after "applying" it, and counts every call."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hub.connectors.http import AmbiguousWrite, ApiError
from hub.domain import BillingStatus, CrmAccount, CrmDeal, ErpCustomer, ErpInvoice, Plan, Term, normalise_domain
from hub.pricing import InvoiceLine, deal_amount

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def make_deal(i: int, **overrides: object) -> CrmDeal:
    plan, seats, term, currency, discount = Plan.TEAM, 10 + i, Term.ANNUAL, "USD", 0
    base = CrmDeal(
        id=f"deal{i}",
        account_id=f"acct{i % 3}",
        name=f"Deal {i}",
        stage="Closed Won",
        amount=deal_amount(plan, seats, term, currency, discount),
        currency=currency,
        close_date=date(2026, 9, 1),
        plan=plan,
        seats=seats,
        term=term,
        discount_pct=discount,
        modified_at=T0 + timedelta(minutes=i),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


class FakeCrm:
    name = "fakecrm"

    def __init__(self, deals: list[CrmDeal]) -> None:
        self.deal_map = {d.id: d for d in deals}
        self.accounts = {
            f"acct{i}": CrmAccount(f"acct{i}", f"Account {i}", f"https://www.acct{i}.com", "US") for i in range(3)
        }
        self.writebacks: list[tuple[str, str | None, BillingStatus]] = []
        self.fail_writeback = False

    def won_deals(self, modified_since: datetime | None) -> Iterator[CrmDeal]:
        for d in sorted(self.deal_map.values(), key=lambda d: d.modified_at):
            if d.is_won and (modified_since is None or d.modified_at > modified_since):
                yield d

    def deals(self) -> Iterator[CrmDeal]:
        yield from self.deal_map.values()

    def deal(self, deal_id: str) -> CrmDeal:
        return self.deal_map[deal_id]

    def account(self, account_id: str) -> CrmAccount:
        return self.accounts[account_id]

    def write_back(self, deal_id: str, invoice_ref: str | None, status: BillingStatus) -> None:
        if self.fail_writeback:
            raise ApiError("fakecrm", 503, "unavailable")
        self.writebacks.append((deal_id, invoice_ref, status))


class FakeErp:
    name = "fakeerp"

    def __init__(self) -> None:
        self.customer_rows: dict[int, ErpCustomer] = {}
        self.invoice_rows: dict[int, ErpInvoice] = {}
        self.calls: dict[str, int] = {}
        self.ambiguous_creates = 0  # the next N creates are applied, then the response is "lost"
        self.fail_creates_with: ApiError | None = None
        self._lock = threading.Lock()

    def _count(self, name: str) -> None:
        with self._lock:
            self.calls[name] = self.calls.get(name, 0) + 1

    def find_customer(self, crm_ref: str, domain: str | None) -> ErpCustomer | None:
        self._count("find_customer")
        for c in self.customer_rows.values():
            if c.crm_ref == crm_ref or (domain and normalise_domain(c.website) == domain):
                return c
        return None

    def create_customer(self, account: CrmAccount, crm_ref: str) -> ErpCustomer:
        self._count("create_customer")
        with self._lock:
            c = ErpCustomer(len(self.customer_rows) + 1, account.name, crm_ref, account.website)
            self.customer_rows[c.id] = c
        return c

    def find_invoices(self, origin: str) -> list[ErpInvoice]:
        self._count("find_invoices")
        return [i for i in self.invoice_rows.values() if i.origin == origin]

    def create_invoice(self, customer: ErpCustomer, deal: CrmDeal, origin: str, line: InvoiceLine) -> ErpInvoice:
        self._count("create_invoice")
        if self.fail_creates_with:
            raise self.fail_creates_with
        with self._lock:
            new_id = len(self.invoice_rows) + 1
            self.invoice_rows[new_id] = None  # type: ignore[assignment]  # reserve the id
        inv = ErpInvoice(
            id=new_id,
            number=None,
            partner_id=customer.id,
            origin=origin,
            amount_untaxed=line.subtotal,
            currency=deal.currency,
            state="draft",
            payment_state="not_paid",
            invoice_date=deal.close_date,
            due_date=None,
        )
        self.invoice_rows[inv.id] = inv
        if self.ambiguous_creates:
            self.ambiguous_creates -= 1
            raise AmbiguousWrite("fakeerp", None, "ReadTimeout after sending")
        return inv

    def post_invoice(self, invoice_id: int) -> ErpInvoice:
        self._count("post_invoice")
        inv = self.invoice_rows[invoice_id]
        posted = replace(inv, state="posted", number=f"INV/2026/{invoice_id:05d}", due_date=date(2099, 1, 1))
        self.invoice_rows[invoice_id] = posted
        return posted

    def invoice(self, invoice_id: int) -> ErpInvoice:
        return self.invoice_rows[invoice_id]

    def invoices(self) -> Iterator[ErpInvoice]:
        yield from self.invoice_rows.values()

    def customers(self) -> Iterator[ErpCustomer]:
        yield from self.customer_rows.values()


def amount_of(deal: CrmDeal) -> Decimal:
    assert deal.plan and deal.seats and deal.term
    return deal_amount(deal.plan, deal.seats, deal.term, deal.currency, deal.discount_pct)
