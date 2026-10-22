"""The HTTP policy and the Odoo and EspoCRM adapters against recorded response shapes (httpx MockTransport)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from hub.connectors.base import PermanentError
from hub.connectors.espocrm import Espo, EspoCrm, to_deal
from hub.connectors.http import AmbiguousWrite, ApiError, Http
from hub.connectors.odoo import Odoo, OdooErp
from hub.domain import BillingStatus, CrmDeal, ErpCustomer, Plan, Term
from hub.pricing import invoice_line


def transport(responses: list[Any], seen: list[httpx.Request]) -> httpx.MockTransport:
    """Each element: an httpx.Response, or an exception to raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler)


def http(responses: list[Any], seen: list[httpx.Request], retries: int = 3) -> Http:
    return Http("test", "http://x", transport=transport(responses, seen), retries=retries, sleep=lambda _s: None)


def test_read_is_retried_on_503_then_succeeds() -> None:
    seen: list[httpx.Request] = []
    h = http([httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": 1})], seen)
    assert h.request("GET", "/a").json() == {"ok": 1}
    assert len(seen) == 3


def test_create_that_timed_out_after_sending_is_ambiguous_not_retried() -> None:
    seen: list[httpx.Request] = []
    h = http([httpx.ReadTimeout("slow")], seen)
    with pytest.raises(AmbiguousWrite):
        h.request("POST", "/create", json={}, idempotent=False)
    assert len(seen) == 1


def test_create_is_retried_when_the_connection_never_opened() -> None:
    seen: list[httpx.Request] = []
    h = http([httpx.ConnectError("refused"), httpx.Response(200, json=[1])], seen)
    assert h.request("POST", "/create", json={}, idempotent=False).json() == [1]


def test_429_is_retried_even_for_a_create_and_honours_retry_after() -> None:
    seen: list[httpx.Request] = []
    slept: list[float] = []
    h = Http(
        "t",
        "http://x",
        transport=transport([httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={})], seen),
        sleep=slept.append,
    )
    h.request("POST", "/c", json={}, idempotent=False)
    assert slept == [3.0]


def test_client_errors_are_not_retried_and_carry_the_vendor_message() -> None:
    seen: list[httpx.Request] = []
    h = http([httpx.Response(422, json={"message": "Invalid field 'foo'"})], seen)
    with pytest.raises(ApiError, match="Invalid field") as err:
        h.request("GET", "/a")
    assert err.value.status == 422 and not err.value.transient and len(seen) == 1


def test_retries_are_bounded() -> None:
    seen: list[httpx.Request] = []
    h = http([httpx.Response(503)] * 3, seen, retries=2)
    with pytest.raises(ApiError) as err:
        h.request("GET", "/a")
    assert err.value.transient and len(seen) == 3


# --- Odoo -------------------------------------------------------------------------------------------------------


def odoo_erp(handler: Any) -> tuple[OdooErp, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        model, method = request.url.path.split("/")[-2:]
        calls.append({"model": model, "method": method, "body": body, "headers": request.headers})
        return httpx.Response(200, json=handler(model, method, body))

    client = Odoo("http://odoo", "odoo", "k3y", transport=httpx.MockTransport(wrapped), sleep=lambda _s: None)
    return OdooErp(client), calls


INVOICE_ROW = {
    "id": 41,
    "name": "INV/2026/00041",
    "partner_id": [7, "Northfield Labs"],
    "invoice_origin": "espocrm:abc",
    "amount_untaxed": 3442.5,
    "currency_id": [1, "USD"],
    "state": "posted",
    "payment_state": "not_paid",
    "invoice_date": "2026-09-01",
    "invoice_date_due": "2026-10-01",
}


def test_odoo_create_invoice_sends_one_untaxed_line_with_the_deal_reference() -> None:
    def handler(model: str, method: str, body: dict[str, Any]) -> Any:
        if method == "create":
            return [41]
        if model == "res.currency":
            return [{"id": 1, "name": "USD"}]
        if model == "product.product":
            return [{"id": 9, "default_code": "LL-TEAM"}]
        if model == "account.payment.term":
            return [{"id": 3, "name": "30 Days"}]
        return [INVOICE_ROW]

    erp, calls = odoo_erp(handler)
    deal = CrmDeal(
        "abc",
        "acc",
        "Northfield - Team",
        "Closed Won",
        Decimal("3442.50"),
        "USD",
        date(2026, 9, 1),
        Plan.TEAM,
        25,
        Term.ANNUAL,
        10,
        __import__("datetime").datetime(2026, 9, 1),
    )
    inv = erp.create_invoice(
        ErpCustomer(7, "Northfield Labs", "espocrm:acc", None),
        deal,
        "espocrm:abc",
        invoice_line(Plan.TEAM, 25, Term.ANNUAL, "USD", 10),
    )
    create = next(c for c in calls if c["method"] == "create")
    values = create["body"]["vals_list"][0]
    assert values["move_type"] == "out_invoice" and values["invoice_origin"] == "espocrm:abc"
    [[_, _, line]] = values["invoice_line_ids"]
    assert line == {
        "product_id": 9,
        "name": "Team plan, 25 seats, annual",
        "quantity": 300,
        "price_unit": "12.75",
        "discount": 10,
        "tax_ids": [[6, 0, []]],
    }
    assert create["headers"]["authorization"] == "bearer k3y" and create["headers"]["x-odoo-database"] == "odoo"
    assert inv.amount_untaxed == Decimal("3442.50") and inv.number == "INV/2026/00041"
    assert inv.billing_status in (BillingStatus.INVOICED, BillingStatus.OVERDUE)


def test_odoo_customer_found_by_reference_then_by_website() -> None:
    def handler(model: str, method: str, body: dict[str, Any]) -> Any:
        if ["ref", "=", "espocrm:a1"] in body["domain"]:
            return []
        return [
            {"id": 5, "name": "Other", "ref": False, "website": "https://other.com"},
            {"id": 6, "name": "Cedarfield", "ref": False, "website": "http://www.cedarfield.com/"},
        ]

    erp, _ = odoo_erp(handler)
    found = erp.find_customer("espocrm:a1", "cedarfield.com")
    assert found is not None and found.id == 6


def test_odoo_missing_catalog_product_is_a_permanent_error() -> None:
    erp, _ = odoo_erp(lambda model, method, body: [])
    with pytest.raises(PermanentError, match="LL-TEAM"):
        erp._product("LL-TEAM")


# --- EspoCRM ----------------------------------------------------------------------------------------------------


def test_espocrm_pages_through_won_deals_with_a_modified_since_filter() -> None:
    seen: list[httpx.Request] = []
    row = {
        "id": "d1",
        "name": "n",
        "accountId": "a",
        "stage": "Closed Won",
        "amount": 100.0,
        "amountCurrency": "USD",
        "closeDate": "2026-09-01",
        "cPlan": "team",
        "cSeats": 5,
        "cTerm": "monthly",
        "cDiscountPct": 0,
        "modifiedAt": "2026-10-01 12:00:00",
    }
    pages = [
        httpx.Response(200, json={"total": 3, "list": [row, {**row, "id": "d2"}]}),
        httpx.Response(200, json={"total": 3, "list": [{**row, "id": "d3"}]}),
    ]
    espo = Espo("http://espo", api_key="key", transport=transport(pages, seen))
    since = __import__("datetime").datetime(2026, 9, 30, tzinfo=__import__("datetime").UTC)
    deals = list(EspoCrm(espo).won_deals(since))
    assert [d.id for d in deals] == ["d1", "d2", "d3"]
    q = seen[0].url.params
    assert q["where[0][attribute]"] == "stage" and q["where[0][value]"] == "Closed Won"
    assert q["where[1][type]"] == "after" and q["where[1][value]"] == "2026-09-30 00:00:00"
    assert seen[1].url.params["offset"] == "2" and seen[0].headers["x-api-key"] == "key"


def test_espocrm_rejects_unknown_enum_values_as_bad_data() -> None:
    row = {"id": "d1", "stage": "Closed Won", "cPlan": "platinum", "modifiedAt": "2026-10-01 12:00:00"}
    with pytest.raises(PermanentError, match="platinum"):
        to_deal(row)


def test_espocrm_write_back_sets_the_two_custom_fields() -> None:
    seen: list[httpx.Request] = []
    espo = Espo("http://espo", api_key="key", transport=transport([httpx.Response(200, json={"id": "d1"})], seen))
    EspoCrm(espo).write_back("d1", "INV/2026/00041", BillingStatus.PAID)
    assert seen[0].method == "PUT" and seen[0].url.path == "/api/v1/Opportunity/d1"
    assert json.loads(seen[0].content) == {"cErpInvoiceRef": "INV/2026/00041", "cBillingStatus": "paid"}


def test_espocrm_in_filter_uses_php_array_syntax() -> None:
    seen: list[httpx.Request] = []
    espo = Espo(
        "http://espo", api_key="key", transport=transport([httpx.Response(200, json={"total": 0, "list": []})], seen)
    )
    espo.list_page("Opportunity", [{"type": "in", "attribute": "stage", "value": ["Proposal", "Negotiation"]}], ["id"])
    assert seen[0].url.params.get_list("where[0][value][]") == ["Proposal", "Negotiation"]
