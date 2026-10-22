"""Against the real systems (`make up && make bootstrap`): a fresh small book goes into EspoCRM, the sync invoices it
in Odoo, a rerun changes nothing, a crash between creating and recording an invoice does not double-invoice, and
the invoice numbers show up on the deals in EspoCRM."""

from __future__ import annotations

import os
import random
from decimal import Decimal

import pytest

from hub import factory
from hub.config import Settings
from hub.connectors.espocrm import Espo
from hub.domain import deal_ref
from hub.seed.generator import generate
from hub.seed.load import load
from hub.sync.engine import SyncEngine

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    if not os.environ.get("ESPOCRM_ADMIN_PASSWORD"):
        pytest.skip("needs the local stack and its .env (make up && make bootstrap)")
    s = Settings()
    factory.store(s).migrate()
    return s


class Crash(BaseException):
    pass


def test_book_is_invoiced_exactly_once_and_written_back(settings: Settings) -> None:
    seed = random.SystemRandom().randrange(10**6)
    book = generate(accounts=8, seed=seed)
    admin = Espo(settings.espocrm_url, admin=("admin", os.environ["ESPOCRM_ADMIN_PASSWORD"]))
    ids = load(book, admin, factory.odoo(settings, admin=True))
    crm, erp, store = factory.crm(settings), factory.erp(settings), factory.store(settings)

    won = [d for d in book.deals if d.stage == "Closed Won"]
    victim = next((d for d in won if not d.data_problem), None)
    assert victim is not None

    def crash(step: str, deal: object) -> None:
        if step == "before_record" and getattr(deal, "id", None) == ids["deals"][victim.key]:
            raise Crash

    with pytest.raises(Crash):
        SyncEngine(crm, erp, store, on_step=crash).run()
    first = SyncEngine(crm, erp, store).run()
    second = SyncEngine(crm, erp, store).run()
    assert second.created == 0

    for d in won:
        espo_id = ids["deals"][d.key]
        invoices = erp.find_invoices(deal_ref("espocrm", espo_id))
        if d.data_problem:
            assert invoices == [], f"{d.key} has a data problem ({d.data_problem}) and must not be invoiced"
            open_ids = {r["deal_id"] for r in store.open_dead_letters("espocrm")}
            assert espo_id in open_ids
            continue
        assert len(invoices) == 1, f"{d.key}: {len(invoices)} invoices"
        [inv] = invoices
        assert inv.state == "posted"
        assert (inv.amount_untaxed, inv.currency) == (Decimal(d.amount), d.currency)
        assert admin.get("Opportunity", espo_id)["cErpInvoiceRef"] == inv.number
    assert first.created + first.linked >= sum(1 for d in won if not d.data_problem) - 1
