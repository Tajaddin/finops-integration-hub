"""Odoo 19 through the External JSON-2 API (`POST /json/2/<model>/<method>`, bearer API key).

Each call is one transaction on the Odoo side: it commits on success and rolls back on error, so a failed call
leaves nothing behind. The integration user only has the Sales and Billing groups (docker/odoo/bootstrap.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from functools import cache
from typing import Any

import httpx

from hub.connectors.base import PermanentError
from hub.connectors.http import Http
from hub.domain import CrmAccount, CrmDeal, ErpCustomer, ErpInvoice, normalise_domain
from hub.pricing import InvoiceLine

INVOICE_FIELDS = [
    "name",
    "partner_id",
    "invoice_origin",
    "amount_untaxed",
    "currency_id",
    "state",
    "payment_state",
    "invoice_date",
    "invoice_date_due",
]
PARTNER_FIELDS = ["name", "ref", "website"]
PAGE = 500


class Odoo:
    """A thin JSON-2 client: one method, `call`."""

    def __init__(
        self,
        url: str,
        database: str,
        api_key: str,
        timeout: float = 20.0,
        retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = None,
    ) -> None:
        headers = {
            "Authorization": f"bearer {api_key}",
            "X-Odoo-Database": database,
            "User-Agent": "finops-integration-hub",
        }
        kwargs: dict[str, Any] = {"transport": transport}
        if sleep is not None:
            kwargs["sleep"] = sleep
        self.http = Http("odoo", url, headers, timeout, retries, **kwargs)

    def call(self, model: str, method: str, *, ids: list[int] | None = None, write: bool = False, **params: Any) -> Any:
        body: dict[str, Any] = dict(params)
        if ids is not None:
            body["ids"] = ids
        response = self.http.request("POST", f"/json/2/{model}/{method}", json=body, idempotent=not write)
        return response.json()

    def search_read(
        self,
        model: str,
        domain: list[Any],
        fields: list[str],
        *,
        limit: int | None = None,
        offset: int = 0,
        order: str = "id",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = self.call(
            model, "search_read", domain=domain, fields=fields, limit=limit, offset=offset, order=order
        )
        return rows

    def close(self) -> None:
        self.http.close()


def _date(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


def to_invoice(row: dict[str, Any]) -> ErpInvoice:
    return ErpInvoice(
        id=row["id"],
        number=row["name"] if row.get("name") not in (False, "/", None) else None,
        partner_id=row["partner_id"][0] if row.get("partner_id") else 0,
        origin=row.get("invoice_origin") or None,
        amount_untaxed=Decimal(str(row["amount_untaxed"])).quantize(Decimal("0.01")),
        currency=row["currency_id"][1] if row.get("currency_id") else "",
        state=row["state"],
        payment_state=row.get("payment_state") or "not_paid",
        invoice_date=_date(row.get("invoice_date")),
        due_date=_date(row.get("invoice_date_due")),
    )


def to_customer(row: dict[str, Any]) -> ErpCustomer:
    return ErpCustomer(row["id"], row["name"], row.get("ref") or None, row.get("website") or None)


class OdooErp:
    """The ERP role, on Odoo: customers are `res.partner` companies, invoices are `account.move` (out_invoice)."""

    name = "odoo"

    def __init__(self, client: Odoo) -> None:
        self.odoo = client
        self._currency = cache(self._currency_id)
        self._product = cache(self._product_id)
        self._country = cache(self._country_id)

    # lookups, cached per instance
    def _currency_id(self, code: str) -> int:
        rows = self.odoo.search_read("res.currency", [["name", "=", code]], ["name"], limit=1)
        if not rows:
            raise PermanentError(f"currency {code} is not active in the ERP")
        return int(rows[0]["id"])

    def _product_id(self, sku: str) -> int:
        rows = self.odoo.search_read("product.product", [["default_code", "=", sku]], ["default_code"], limit=1)
        if not rows:
            raise PermanentError(f"product {sku} is missing from the ERP catalog")
        return int(rows[0]["id"])

    def _country_id(self, code: str) -> int | None:
        rows = self.odoo.search_read("res.country", [["code", "=", code.upper()]], ["code"], limit=1)
        return int(rows[0]["id"]) if rows else None

    # customers
    def find_customer(self, crm_ref: str, domain: str | None) -> ErpCustomer | None:
        rows = self.odoo.search_read(
            "res.partner", [["ref", "=", crm_ref], ["is_company", "=", True]], PARTNER_FIELDS, limit=1
        )
        if rows:
            return to_customer(rows[0])
        if domain:
            candidates = self.odoo.search_read(
                "res.partner", [["website", "ilike", domain], ["is_company", "=", True]], PARTNER_FIELDS, limit=20
            )
            for row in candidates:
                if normalise_domain(row.get("website") or None) == domain:
                    return to_customer(row)
        return None

    def create_customer(self, account: CrmAccount, crm_ref: str) -> ErpCustomer:
        values: dict[str, Any] = {"name": account.name, "ref": crm_ref, "is_company": True, "customer_rank": 1}
        if account.website:
            values["website"] = account.website
        if account.country and (country := self._country(account.country)):
            values["country_id"] = country
        ids = self.odoo.call("res.partner", "create", vals_list=[values], write=True)
        return ErpCustomer(int(ids[0]), account.name, crm_ref, account.website)

    def customers(self) -> Iterator[ErpCustomer]:
        offset = 0
        while True:
            rows = self.odoo.search_read(
                "res.partner",
                [["is_company", "=", True], ["customer_rank", ">", 0]],
                PARTNER_FIELDS,
                limit=PAGE,
                offset=offset,
            )
            yield from (to_customer(r) for r in rows)
            if len(rows) < PAGE:
                return
            offset += PAGE

    # invoices
    def find_invoices(self, origin: str) -> list[ErpInvoice]:
        rows = self.odoo.search_read(
            "account.move", [["move_type", "=", "out_invoice"], ["invoice_origin", "=", origin]], INVOICE_FIELDS
        )
        return [to_invoice(r) for r in rows]

    def create_invoice(self, customer: ErpCustomer, deal: CrmDeal, origin: str, line: InvoiceLine) -> ErpInvoice:
        values = {
            "move_type": "out_invoice",
            "partner_id": customer.id,
            "currency_id": self._currency(deal.currency),
            "invoice_date": (deal.close_date or date.today()).isoformat(),
            "invoice_payment_term_id": self._net30(),
            "invoice_origin": origin,
            "ref": deal.name[:120],
            "invoice_line_ids": [
                [
                    0,
                    0,
                    {
                        "product_id": self._product(line.sku),
                        "name": line.description,
                        "quantity": line.quantity,
                        "price_unit": str(line.unit_price),
                        "discount": line.discount_pct,
                        "tax_ids": [[6, 0, []]],  # untaxed by design (ADR 0002)
                    },
                ]
            ],
        }
        ids = self.odoo.call("account.move", "create", vals_list=[values], write=True)
        return self.invoice(int(ids[0]))

    def _net30(self) -> int | bool:
        rows = self.odoo.search_read("account.payment.term", [["name", "=", "30 Days"]], ["name"], limit=1)
        return int(rows[0]["id"]) if rows else False

    def post_invoice(self, invoice_id: int) -> ErpInvoice:
        self.odoo.call("account.move", "action_post", ids=[invoice_id], write=True)
        return self.invoice(invoice_id)

    def invoice(self, invoice_id: int) -> ErpInvoice:
        rows = self.odoo.search_read("account.move", [["id", "=", invoice_id]], INVOICE_FIELDS, limit=1)
        if not rows:
            raise PermanentError(f"invoice {invoice_id} not found")
        return to_invoice(rows[0])

    def invoices(self) -> Iterator[ErpInvoice]:
        offset = 0
        while True:
            rows = self.odoo.search_read(
                "account.move", [["move_type", "=", "out_invoice"]], INVOICE_FIELDS, limit=PAGE, offset=offset
            )
            yield from (to_invoice(r) for r in rows)
            if len(rows) < PAGE:
                return
            offset += PAGE

    # used by the data generator and fault injector (run as an admin key, never by the sync)
    def register_payment(self, invoice_id: int, amount: Decimal, when: date) -> None:
        wizard = self.odoo.call(
            "account.payment.register",
            "create",
            vals_list=[{"amount": str(amount), "payment_date": when.isoformat()}],
            context={"active_model": "account.move", "active_ids": [invoice_id]},
            write=True,
        )
        self.odoo.call(
            "account.payment.register",
            "action_create_payments",
            ids=wizard,
            context={"active_model": "account.move", "active_ids": [invoice_id]},
            write=True,
        )

    def cancel_invoice(self, invoice_id: int) -> None:
        self.odoo.call("account.move", "button_draft", ids=[invoice_id], write=True)
        self.odoo.call("account.move", "button_cancel", ids=[invoice_id], write=True)

    def set_line_price(self, invoice_id: int, unit_price: Decimal) -> None:
        """Re-price the invoice's line (a manual edit in the ERP, for fault injection)."""
        lines = self.odoo.search_read(
            "account.move.line", [["move_id", "=", invoice_id], ["display_type", "=", "product"]], ["id"], limit=1
        )
        self.odoo.call("account.move", "button_draft", ids=[invoice_id], write=True)
        self.odoo.call(
            "account.move.line", "write", ids=[lines[0]["id"]], vals={"price_unit": str(unit_price)}, write=True
        )
        self.odoo.call("account.move", "action_post", ids=[invoice_id], write=True)
