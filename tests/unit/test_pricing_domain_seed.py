from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from hub.domain import BillingStatus, ErpInvoice, Plan, Term, normalise_domain
from hub.pricing import deal_amount, invoice_line, unit_price
from hub.seed.generator import generate


def test_annual_prices_are_exact_cents() -> None:
    assert unit_price(Plan.TEAM, Term.ANNUAL, "USD") == Decimal("12.75")
    assert unit_price(Plan.STARTER, Term.ANNUAL, "GBP") == Decimal("5.95")
    assert unit_price(Plan.ENTERPRISE, Term.MONTHLY, "EUR") == Decimal("42.00")


def test_amount_is_seats_times_months_times_price_less_discount() -> None:
    # 25 seats x 12 months x 12.75 = 3825.00; 10% off = 3442.50
    assert deal_amount(Plan.TEAM, 25, Term.ANNUAL, "USD", 10) == Decimal("3442.50")
    line = invoice_line(Plan.TEAM, 25, Term.ANNUAL, "USD", 10)
    assert (line.quantity, line.unit_price, line.discount_pct) == (300, Decimal("12.75"), 10)


def test_price_book_never_needs_rounding() -> None:
    # The list prices are chosen so seat-months x unit price x (1 - discount) is always a whole number of cents:
    # nothing for the hub's rounding and the ERP's rounding to disagree about.
    from hub.pricing import ALLOWED_DEAL_DISCOUNTS, MONTHLY_SEAT_PRICE

    for currency, plans in MONTHLY_SEAT_PRICE.items():
        for plan in plans:
            for term in Term:
                for discount in ALLOWED_DEAL_DISCOUNTS:
                    for seats in (1, 3, 7, 13, 250):
                        line = invoice_line(plan, seats, term, currency, discount)
                        exact = Decimal(line.quantity) * line.unit_price * (100 - discount) / 100
                        assert exact == exact.quantize(Decimal("0.01")), (currency, plan, term, discount, seats)
    assert invoice_line(Plan.STARTER, 7, Term.ANNUAL, "GBP", 5).subtotal == Decimal("474.81")


@pytest.mark.parametrize(
    ("plan", "seats", "currency", "discount"),
    [(Plan.TEAM, 0, "USD", 0), (Plan.TEAM, 5, "JPY", 0), (Plan.TEAM, 5, "USD", 7)],
)
def test_invalid_inputs_are_refused(plan: Plan, seats: int, currency: str, discount: int) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        invoice_line(plan, seats, Term.MONTHLY, currency, discount)


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.Example.com/about", "example.com"),
        ("http://acme.co.uk:8080", "acme.co.uk"),
        ("acme.io", "acme.io"),
        ("", None),
        (None, None),
    ],
)
def test_normalise_domain(url: str | None, domain: str | None) -> None:
    assert normalise_domain(url) == domain


def _inv(**kw: object) -> ErpInvoice:
    base = {
        "id": 1,
        "number": "INV/1",
        "partner_id": 1,
        "origin": "x",
        "amount_untaxed": Decimal(1),
        "currency": "USD",
        "state": "posted",
        "payment_state": "not_paid",
        "invoice_date": date.today(),
        "due_date": date.today() + timedelta(days=30),
    }
    base.update(kw)
    return ErpInvoice(**base)  # type: ignore[arg-type]


def test_billing_status_from_the_invoice() -> None:
    assert _inv().billing_status is BillingStatus.INVOICED
    assert _inv(payment_state="paid").billing_status is BillingStatus.PAID
    assert _inv(payment_state="partial").billing_status is BillingStatus.PARTIAL
    assert _inv(due_date=date.today() - timedelta(days=1)).billing_status is BillingStatus.OVERDUE
    assert _inv(state="cancel").billing_status is BillingStatus.CANCELLED


def test_seed_is_reproducible_and_amounts_follow_the_price_book() -> None:
    a, b = generate(accounts=40, seed=3), generate(accounts=40, seed=3)
    assert a.as_dict() == b.as_dict()
    assert generate(accounts=40, seed=4).as_dict() != a.as_dict()
    for d in a.deals:
        if d.data_problem or not d.plan:
            continue
        assert Decimal(d.amount) == deal_amount(Plan(d.plan), d.seats, Term(d.term), d.currency, d.discount_pct)
    m = a.manifest()
    assert m["accounts"] == 40 and m["won_deals"] > 0
    assert len({x.website for x in a.accounts}) == 40  # websites are unique, so domain matching is unambiguous
