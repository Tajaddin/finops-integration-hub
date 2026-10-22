"""Load a generated book into EspoCRM (as the sales team would, with admin credentials) and pre-create the
customers finance already had in Odoo. Returns the mapping from seed keys to the systems' own ids."""

from __future__ import annotations

from typing import Any

from hub import log
from hub.connectors.espocrm import Espo
from hub.connectors.odoo import Odoo
from hub.seed.generator import Book

logger = log.get(__name__)


def load(book: Book, espo_admin: Espo, odoo_admin: Odoo) -> dict[str, Any]:
    account_ids: dict[str, str] = {}
    for a in book.accounts:
        row = espo_admin.create(
            "Account",
            {"name": a.name, "website": a.website, "billingAddressCountry": a.country, "type": "Customer"},
            skip_duplicate_check=True,
        )
        account_ids[a.key] = row["id"]
        if a.in_erp_already:  # entered by finance by hand, before the integration: no CRM reference, same website
            odoo_admin.call(
                "res.partner",
                "create",
                vals_list=[{"name": a.name, "is_company": True, "website": a.website, "customer_rank": 1}],
                write=True,
            )
    deal_ids: dict[str, str] = {}
    for d in book.deals:
        values: dict[str, Any] = {
            "name": d.name,
            "accountId": account_ids[d.account_key],
            "stage": d.stage,
            "amount": float(d.amount),
            "amountCurrency": d.currency,
            "closeDate": d.close_date,
            "cPlan": d.plan or "",
            "cSeats": d.seats,
            "cTerm": d.term,
            "cDiscountPct": d.discount_pct,
        }
        row = espo_admin.create("Opportunity", values, skip_duplicate_check=True)
        deal_ids[d.key] = row["id"]
    logger.info("seed_loaded", accounts=len(account_ids), deals=len(deal_ids))
    return {"accounts": account_ids, "deals": deal_ids}
