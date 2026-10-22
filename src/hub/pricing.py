"""The price book: one place for list prices, term discounts and rounding, shared by the data generator, the sync
(which turns a deal into invoice lines) and the reconciliation (which checks the invoice against the deal).

Prices are per seat per month in each currency's own price list (not converted at a rate), the way SaaS price
books usually work. Annual terms are billed up front at 15% off; monthly terms bill the first month. Tax is out of
scope: invoices are untaxed amounts (ADR 0002).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from hub.domain import Plan, Term

MONTHLY_SEAT_PRICE: dict[str, dict[Plan, Decimal]] = {
    "USD": {
        Plan.STARTER: Decimal("8.00"),
        Plan.TEAM: Decimal("15.00"),
        Plan.BUSINESS: Decimal("28.00"),
        Plan.ENTERPRISE: Decimal("45.00"),
    },
    "EUR": {
        Plan.STARTER: Decimal("8.00"),
        Plan.TEAM: Decimal("14.00"),
        Plan.BUSINESS: Decimal("26.00"),
        Plan.ENTERPRISE: Decimal("42.00"),
    },
    "GBP": {
        Plan.STARTER: Decimal("7.00"),
        Plan.TEAM: Decimal("12.00"),
        Plan.BUSINESS: Decimal("22.00"),
        Plan.ENTERPRISE: Decimal("36.00"),
    },
}
ANNUAL_DISCOUNT = Decimal("0.15")
ALLOWED_DEAL_DISCOUNTS = (0, 5, 10, 15, 20)  # percent; anything else needs a finance approval outside this system
CENT = Decimal("0.01")

SKU = {plan: f"LL-{plan.value.upper()}" for plan in Plan}


@dataclass(frozen=True)
class InvoiceLine:
    sku: str
    description: str
    quantity: int  # seat-months
    unit_price: Decimal  # per seat-month, after the term discount
    discount_pct: int  # the deal's discount, applied on the line as the ERP does

    @property
    def subtotal(self) -> Decimal:
        gross = Decimal(self.quantity) * self.unit_price
        return (gross * (Decimal(100) - self.discount_pct) / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP)


def months(term: Term) -> int:
    return 12 if term is Term.ANNUAL else 1


def unit_price(plan: Plan, term: Term, currency: str) -> Decimal:
    if currency not in MONTHLY_SEAT_PRICE:
        raise ValueError(f"no price list for currency {currency!r}")
    base = MONTHLY_SEAT_PRICE[currency][plan]
    if term is Term.ANNUAL:
        base = base * (1 - ANNUAL_DISCOUNT)
    return base.quantize(CENT, rounding=ROUND_HALF_UP)


def invoice_line(plan: Plan, seats: int, term: Term, currency: str, discount_pct: int) -> InvoiceLine:
    if seats < 1:
        raise ValueError("seats must be at least 1")
    if discount_pct not in ALLOWED_DEAL_DISCOUNTS:
        raise ValueError(f"discount {discount_pct}% is not in the approved list {ALLOWED_DEAL_DISCOUNTS}")
    label = f"{plan.value.title()} plan, {seats} seats, {term.value}"
    return InvoiceLine(SKU[plan], label, seats * months(term), unit_price(plan, term, currency), discount_pct)


def deal_amount(plan: Plan, seats: int, term: Term, currency: str, discount_pct: int) -> Decimal:
    """What the deal should be worth: the subtotal of the one line the sync will invoice."""
    return invoice_line(plan, seats, term, currency, discount_pct).subtotal
