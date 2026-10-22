"""Every reconciliation class from hand-built inputs, and silence on a book where both sides agree."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from hub.domain import BillingStatus, ErpCustomer, ErpInvoice
from hub.recon.faults import score
from hub.recon.rules import KINDS, reconcile
from tests.fakes import make_deal

CRM = "fakecrm"


def invoice(i: int, deal_id: str | None, amount: Decimal, **kw: object) -> ErpInvoice:
    base = ErpInvoice(
        id=i,
        number=f"INV/{i}",
        partner_id=1,
        origin=f"{CRM}:{deal_id}" if deal_id else None,
        amount_untaxed=amount,
        currency="USD",
        state="posted",
        payment_state="not_paid",
        invoice_date=date.today(),
        due_date=date.today() + timedelta(days=30),
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def clean_book() -> tuple[list, list, list]:
    deals = [make_deal(i, billing_status=BillingStatus.INVOICED) for i in range(5)]
    invoices = [invoice(100 + i, d.id, d.amount) for i, d in enumerate(deals)]
    customers = [
        ErpCustomer(1, "A", "fakecrm:acct0", "https://a.com"),
        ErpCustomer(2, "B", "fakecrm:acct1", "https://b.com"),
    ]
    return deals, invoices, customers


def kinds(exceptions: list) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in exceptions:
        out.setdefault(e.kind, []).append(e.key)
    return out


def test_agreeing_systems_produce_no_exceptions() -> None:
    assert reconcile(CRM, *clean_book()) == []


def test_each_class_is_found_with_its_key() -> None:
    deals, invoices, customers = clean_book()
    deals[0] = replace(deals[0], amount=deals[0].amount + 1)  # amount
    deals[1] = replace(deals[1], currency="EUR")  # currency (reported instead of amount)
    invoices[2] = replace(invoices[2], payment_state="paid")  # CRM still says invoiced
    invoices[3] = replace(invoices[3], state="cancel")  # cancelled on a won deal
    invoices.append(invoice(200, deals[4].id, deals[4].amount))  # second live invoice
    deals.append(make_deal(7))  # won, never invoiced
    deals.append(make_deal(8, stage="Closed Lost"))
    invoices.append(invoice(201, "deal8", Decimal("10.00")))  # invoice for a lost deal
    invoices.append(invoice(202, "ghost", Decimal("10.00")))  # invoice for a deal that does not exist
    customers.append(ErpCustomer(3, "A again", None, "http://www.a.com/"))
    found = kinds(reconcile(CRM, deals, invoices, customers, {"deal7": "missing plan"}))
    assert found == {
        "missing_invoice": ["deal7"],
        "cancelled_invoice": ["deal3"],
        "duplicate_invoice": ["deal4"],
        "amount_mismatch": ["deal0"],
        "currency_mismatch": ["deal1"],
        "orphan_invoice": ["201", "202"],
        "duplicate_customer": ["1,3"],
        "billing_status_drift": ["deal2"],
    }


def test_missing_invoice_carries_the_dead_letter_reason() -> None:
    deals, invoices, customers = clean_book()
    deals.append(make_deal(9, plan=None))
    [exc] = reconcile(CRM, deals, invoices, customers, {"deal9": "missing plan"})
    assert "missing plan" in exc.summary and exc.queue == "Finance Ops" and exc.severity == "high"


def test_cancelled_duplicates_do_not_count_and_other_crms_are_ignored() -> None:
    deals, invoices, customers = clean_book()
    invoices.append(invoice(300, deals[0].id, deals[0].amount, state="cancel"))
    invoices.append(invoice(301, None, Decimal("5.00")))  # a manual invoice, no CRM origin
    invoices.append(replace(invoice(302, "x", Decimal("5.00")), origin="salesforce:006xx"))
    assert reconcile(CRM, deals, invoices, customers) == []


def test_fingerprints_are_stable_and_distinct() -> None:
    deals, invoices, customers = clean_book()
    deals[0] = replace(deals[0], amount=Decimal("1.00"))
    a = reconcile(CRM, deals, invoices, customers)
    b = reconcile(CRM, deals, invoices, customers)
    assert [e.fingerprint for e in a] == [e.fingerprint for e in b]
    deals[1] = replace(deals[1], amount=Decimal("1.00"))
    assert len({e.fingerprint for e in reconcile(CRM, deals, invoices, customers)}) == 2


def test_score_counts_precision_and_recall_per_class() -> None:
    truth = {("amount_mismatch", "a"), ("amount_mismatch", "b"), ("orphan_invoice", "1")}
    detected = {("amount_mismatch", "a"), ("orphan_invoice", "1"), ("orphan_invoice", "2")}
    s = score(detected, truth, KINDS)
    assert s["per_class"]["amount_mismatch"]["recall"] == 0.5
    assert s["per_class"]["orphan_invoice"]["precision"] == 0.5
    assert s["overall"] == {"truth": 3, "detected": 3, "precision": 0.6667, "recall": 0.6667}
    assert s["false_negatives"] == [("amount_mismatch", "b")]
