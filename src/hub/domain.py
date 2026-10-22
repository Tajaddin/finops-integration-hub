"""The order-to-cash records the hub moves between systems, independent of any vendor's field names."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Plan(StrEnum):
    STARTER = "starter"
    TEAM = "team"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class Term(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class BillingStatus(StrEnum):
    """What the CRM shows the account manager about a won deal."""

    NOT_INVOICED = "not_invoiced"
    INVOICED = "invoiced"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


CURRENCIES = ("USD", "EUR", "GBP")


@dataclass(frozen=True)
class CrmAccount:
    id: str
    name: str
    website: str | None
    country: str | None


@dataclass(frozen=True)
class CrmDeal:
    id: str
    account_id: str
    name: str
    stage: str
    amount: Decimal
    currency: str
    close_date: date | None
    plan: Plan | None
    seats: int | None
    term: Term | None
    discount_pct: int
    modified_at: datetime
    erp_invoice_ref: str | None = None
    billing_status: BillingStatus | None = None

    @property
    def is_won(self) -> bool:
        return self.stage == "Closed Won"


@dataclass(frozen=True)
class ErpCustomer:
    id: int
    name: str
    crm_ref: str | None
    website: str | None


@dataclass(frozen=True)
class ErpInvoice:
    id: int
    number: str | None
    partner_id: int
    origin: str | None
    amount_untaxed: Decimal
    currency: str
    state: str  # draft | posted | cancel
    payment_state: str  # not_paid | in_payment | paid | partial | reversed | ...
    invoice_date: date | None
    due_date: date | None

    @property
    def billing_status(self) -> BillingStatus:
        today = date.today()
        if self.state == "cancel":
            return BillingStatus.CANCELLED
        if self.payment_state in ("paid", "in_payment"):
            return BillingStatus.PAID
        if self.payment_state == "partial":
            return BillingStatus.PARTIAL
        if self.state == "posted" and self.due_date is not None and self.due_date < today:
            return BillingStatus.OVERDUE
        return BillingStatus.INVOICED


def normalise_domain(website: str | None) -> str | None:
    """`https://www.Example.com/about` -> `example.com`; used to match a CRM account to an existing ERP customer."""
    if not website:
        return None
    host = website.strip().lower()
    for prefix in ("https://", "http://"):
        host = host.removeprefix(prefix)
    host = host.split("/", 1)[0].split(":", 1)[0].removeprefix("www.")
    return host or None


def deal_ref(crm: str, deal_id: str) -> str:
    """The key that ties an ERP invoice to its CRM deal (stored in the invoice's source document field)."""
    return f"{crm}:{deal_id}"


def at_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)
