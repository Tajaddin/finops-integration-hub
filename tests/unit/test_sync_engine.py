"""The sync's guarantees, against in-memory systems and a real Postgres for its own state."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from hub.connectors.http import ApiError
from hub.domain import BillingStatus, Plan
from hub.store import AlreadyRunning, Store
from hub.sync.engine import SyncEngine, validate
from tests.fakes import FakeCrm, FakeErp, amount_of, make_deal


class Crash(BaseException):
    """Not an Exception, so nothing in the engine can swallow it: the process "dies" here."""


def engine(crm: FakeCrm, erp: FakeErp, store: Store, crash_at: tuple[str, str] | None = None) -> SyncEngine:
    def on_step(step: str, deal: object) -> None:
        if crash_at and step == crash_at[0] and getattr(deal, "id", None) == crash_at[1]:
            raise Crash(step)

    return SyncEngine(crm, erp, store, on_step=on_step)


def test_every_won_deal_becomes_one_posted_invoice(store: Store) -> None:
    deals = [make_deal(i) for i in range(6)] + [make_deal(9, stage="Closed Lost")]
    crm, erp = FakeCrm(deals), FakeErp()
    stats = engine(crm, erp, store).run()
    assert (stats.seen, stats.created, stats.dead_lettered) == (6, 6, 0)
    assert {i.state for i in erp.invoice_rows.values()} == {"posted"}
    for d in deals[:6]:
        [inv] = erp.find_invoices(f"fakecrm:{d.id}")
        assert inv.amount_untaxed == amount_of(d) == d.amount
    assert stats.customers_created == 3  # deals share three accounts
    assert len(crm.writebacks) == 6 and {s for _, _, s in crm.writebacks} == {BillingStatus.INVOICED}


def test_rerun_creates_nothing(store: Store) -> None:
    crm, erp = FakeCrm([make_deal(i) for i in range(5)]), FakeErp()
    engine(crm, erp, store).run()
    stats = engine(crm, erp, store).run(full=True)
    assert (stats.created, stats.unchanged) == (0, 5)
    assert len(erp.invoice_rows) == 5
    assert stats.writebacks_sent == 0  # nothing changed, nothing re-sent


@pytest.mark.parametrize("step", ["before_post", "before_record"])
def test_crash_mid_deal_never_double_invoices(store: Store, step: str) -> None:
    crm, erp = FakeCrm([make_deal(i) for i in range(4)]), FakeErp()
    with pytest.raises(Crash):
        engine(crm, erp, store, crash_at=(step, "deal2")).run()
    assert len(erp.invoice_rows) == 3  # deal0, deal1 done; deal2's invoice exists, unrecorded
    stats = engine(crm, erp, store).run()
    assert len(erp.invoice_rows) == 4, "deal2 must be linked, not invoiced twice"
    assert {i.state for i in erp.invoice_rows.values()} == {"posted"}
    assert stats.linked == 1 and stats.created == 1  # deal2 linked (or posted then linked), deal3 created
    assert set(store.links("fakecrm")) == {f"deal{i}" for i in range(4)}


def test_lost_response_after_create_is_resolved_by_lookup(store: Store) -> None:
    crm, erp = FakeCrm([make_deal(0)]), FakeErp()
    erp.ambiguous_creates = 1
    stats = engine(crm, erp, store).run()
    assert stats.created == 1 and len(erp.invoice_rows) == 1
    assert erp.calls["create_invoice"] == 1, "an ambiguous create must never be retried blindly"


def test_bad_data_is_dead_lettered_with_the_reason_and_the_run_continues(store: Store) -> None:
    good = make_deal(0)
    typo = make_deal(1, amount=make_deal(1).amount * 10)
    no_plan = make_deal(2, plan=None)
    discount = make_deal(3, discount_pct=25)
    euro = make_deal(4, currency="JPY")
    crm, erp = FakeCrm([good, typo, no_plan, discount, euro]), FakeErp()
    stats = engine(crm, erp, store).run()
    assert (stats.created, stats.dead_lettered) == (1, 4)
    reasons = {r["deal_id"]: r["detail"] for r in store.open_dead_letters("fakecrm")}
    assert "differs from the price book" in reasons["deal1"]
    assert "missing plan" in reasons["deal2"]
    assert "not an approved discount" in reasons["deal3"]
    assert "no price list" in reasons["deal4"]


def test_fixing_a_dead_letter_in_the_crm_resolves_it(store: Store) -> None:
    deal = make_deal(1, plan=None)
    crm, erp = FakeCrm([deal]), FakeErp()
    engine(crm, erp, store).run()
    assert len(store.open_dead_letters("fakecrm")) == 1
    crm.deal_map["deal1"] = replace(make_deal(1), modified_at=deal.modified_at.replace(hour=10))
    stats = engine(crm, erp, store).run()
    assert stats.created == 1
    assert store.open_dead_letters("fakecrm") == []


def test_transient_erp_failure_is_parked_for_the_next_run(store: Store) -> None:
    crm, erp = FakeCrm([make_deal(0)]), FakeErp()
    erp.fail_creates_with = ApiError("fakeerp", 503, "maintenance")
    stats = engine(crm, erp, store).run()
    assert stats.dead_lettered == 1
    assert store.open_dead_letters("fakecrm")[0]["reason"] == "transient"
    erp.fail_creates_with = None
    stats = engine(crm, erp, store).run(full=True)
    assert stats.created == 1 and store.open_dead_letters("fakecrm") == []


def test_cancelled_invoice_is_not_reissued(store: Store) -> None:
    crm, erp = FakeCrm([make_deal(0)]), FakeErp()
    engine(crm, erp, store).run()
    erp.invoice_rows[1] = replace(erp.invoice_rows[1], state="cancel")
    stats = engine(crm, erp, store).run(full=True)
    assert stats.skipped_cancelled == 1 and len(erp.invoice_rows) == 1


def test_failed_writeback_stays_in_the_outbox_until_delivered(store: Store) -> None:
    crm, erp = FakeCrm([make_deal(0)]), FakeErp()
    crm.fail_writeback = True
    stats = engine(crm, erp, store).run()
    assert (stats.created, stats.writebacks_failed) == (1, 1)
    crm.fail_writeback = False
    stats = engine(crm, erp, store).run()
    assert stats.writebacks_sent == 1 and crm.writebacks[0][0] == "deal0"


def test_existing_erp_customer_is_matched_by_website_not_duplicated(store: Store) -> None:
    crm, erp = FakeCrm([make_deal(0)]), FakeErp()
    from hub.domain import ErpCustomer

    erp.customer_rows[1] = ErpCustomer(1, "Account 0 (entered by finance)", None, "http://acct0.com/")
    stats = engine(crm, erp, store).run()
    assert stats.customers_created == 0
    assert next(iter(erp.invoice_rows.values())).partner_id == 1


def test_only_one_run_at_a_time(store: Store) -> None:
    with store.lock("sync:fakecrm"), pytest.raises(AlreadyRunning):
        engine(FakeCrm([]), FakeErp(), store).run()


def test_cursor_skips_deals_seen_before(store: Store) -> None:
    crm, erp = FakeCrm([make_deal(i) for i in (0, 10, 20)]), FakeErp()  # modified T0, T0+10 min, T0+20 min
    engine(crm, erp, store).run()
    later = make_deal(50)
    crm.deal_map[later.id] = later
    stats = engine(crm, erp, store).run()
    # cursor T0+20 min minus the 5-minute overlap: deal20 is read again (and left alone), deal50 is new
    assert (stats.seen, stats.unchanged, stats.created) == (2, 1, 1)


def test_validate_accepts_price_book_amounts() -> None:
    validate(make_deal(0, plan=Plan.ENTERPRISE, amount=amount_of(make_deal(0, plan=Plan.ENTERPRISE))))
    with pytest.raises(Exception, match="price book"):
        validate(make_deal(0, amount=Decimal("1.00")))
