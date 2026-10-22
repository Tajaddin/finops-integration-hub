"""EspoCRM 10 REST API (`/api/v1`), signed in as an API user (X-Api-Key) whose role can read and edit Accounts and
Opportunities and nothing else (`hub bootstrap espocrm`).

The order-to-cash fields the deal needs (plan, seats, term, discount) and the two the hub writes back (ERP invoice
number, billing status) are custom fields; EspoCRM prefixes custom field names with `c`.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx

from hub.connectors.base import PermanentError
from hub.connectors.http import Http
from hub.domain import BillingStatus, CrmAccount, CrmDeal, Plan, Term

DEAL_FIELDS = [
    "id",
    "name",
    "accountId",
    "stage",
    "amount",
    "amountCurrency",
    "closeDate",
    "cPlan",
    "cSeats",
    "cTerm",
    "cDiscountPct",
    "cErpInvoiceRef",
    "cBillingStatus",
    "modifiedAt",
]
ACCOUNT_FIELDS = ["id", "name", "website", "billingAddressCountry"]
PAGE = 200  # EspoCRM's maximum maxSize

# Custom fields the bootstrap creates: (entity, name without the c prefix, definition)
CUSTOM_FIELDS: list[tuple[str, str, dict[str, Any]]] = [
    ("Opportunity", "plan", {"type": "enum", "options": ["", *[p.value for p in Plan]], "label": "Plan"}),
    ("Opportunity", "seats", {"type": "int", "min": 0, "label": "Seats"}),
    ("Opportunity", "term", {"type": "enum", "options": ["", *[t.value for t in Term]], "label": "Billing term"}),
    ("Opportunity", "discountPct", {"type": "int", "min": 0, "max": 100, "default": 0, "label": "Discount %"}),
    ("Opportunity", "erpInvoiceRef", {"type": "varchar", "maxLength": 64, "label": "ERP invoice"}),
    (
        "Opportunity",
        "billingStatus",
        {"type": "enum", "options": ["", *[s.value for s in BillingStatus]], "label": "Billing status"},
    ),
]


def espo_time(value: str) -> datetime:
    """EspoCRM stores datetimes in UTC as `YYYY-MM-DD HH:MM:SS`."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def to_espo_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def to_deal(row: dict[str, Any]) -> CrmDeal:
    def enum[E: (Plan, Term, BillingStatus)](cls: type[E], value: Any) -> E | None:
        try:
            return cls(value) if value else None
        except ValueError as exc:
            raise PermanentError(f"deal {row['id']}: {value!r} is not a valid {cls.__name__}") from exc

    return CrmDeal(
        id=row["id"],
        account_id=row.get("accountId") or "",
        name=row.get("name") or "",
        stage=row.get("stage") or "",
        amount=Decimal(str(row.get("amount") or 0)).quantize(Decimal("0.01")),
        currency=row.get("amountCurrency") or "",
        close_date=date.fromisoformat(row["closeDate"]) if row.get("closeDate") else None,
        plan=enum(Plan, row.get("cPlan")),
        seats=row.get("cSeats"),
        term=enum(Term, row.get("cTerm")),
        discount_pct=int(row.get("cDiscountPct") or 0),
        modified_at=espo_time(row["modifiedAt"]),
        erp_invoice_ref=row.get("cErpInvoiceRef") or None,
        billing_status=enum(BillingStatus, row.get("cBillingStatus")),
    )


class Espo:
    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        admin: tuple[str, str] | None = None,
        timeout: float = 20.0,
        retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"User-Agent": "finops-integration-hub", "Content-Type": "application/json"}
        if api_key:
            headers["X-Api-Key"] = api_key
        elif admin:  # bootstrap and data generation only
            headers["Espo-Authorization"] = base64.b64encode(f"{admin[0]}:{admin[1]}".encode()).decode()
        else:
            raise ValueError("EspoCRM needs an API key or admin credentials")
        self.http = Http("espocrm", url.rstrip("/") + "/api/v1", headers, timeout, retries, transport=transport)

    def list_page(
        self,
        entity: str,
        where: list[dict[str, Any]],
        select: list[str],
        *,
        offset: int = 0,
        order_by: str = "modifiedAt",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "maxSize": PAGE,
            "offset": offset,
            "orderBy": order_by,
            "order": "asc",
            "select": ",".join(select),
        }
        for i, clause in enumerate(where):
            for key, value in clause.items():
                # list values (the `in` filter) go as where[i][value][]=a&where[i][value][]=b, PHP's array syntax
                params[f"where[{i}][{key}]" + ("[]" if isinstance(value, list) else "")] = value
        data: dict[str, Any] = self.http.request("GET", f"/{entity}", params=params).json()
        return data

    def iterate(self, entity: str, where: list[dict[str, Any]], select: list[str]) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            page = self.list_page(entity, where, select, offset=offset)
            rows = page.get("list", [])
            yield from rows
            offset += len(rows)
            if not rows or offset >= int(page.get("total", 0)):
                return

    def get(self, entity: str, record_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self.http.request("GET", f"/{entity}/{record_id}").json()
        return data

    def create(self, entity: str, values: dict[str, Any], *, skip_duplicate_check: bool = False) -> dict[str, Any]:
        """`skip_duplicate_check` for bulk loads: EspoCRM otherwise answers 409 when a record with the same name
        exists (it is a prompt for a person, not an integrity rule)."""
        headers = {"X-Skip-Duplicate-Check": "true"} if skip_duplicate_check else None
        data: dict[str, Any] = self.http.request(
            "POST", f"/{entity}", json=values, headers=headers, idempotent=False
        ).json()
        return data

    def update(self, entity: str, record_id: str, values: dict[str, Any]) -> dict[str, Any]:
        data: dict[str, Any] = self.http.request("PUT", f"/{entity}/{record_id}", json=values).json()
        return data

    def close(self) -> None:
        self.http.close()


class EspoCrm:
    """The CRM role, on EspoCRM."""

    name = "espocrm"

    def __init__(self, client: Espo) -> None:
        self.espo = client

    def won_deals(self, modified_since: datetime | None) -> Iterator[CrmDeal]:
        where: list[dict[str, Any]] = [{"type": "equals", "attribute": "stage", "value": "Closed Won"}]
        if modified_since is not None:
            where.append({"type": "after", "attribute": "modifiedAt", "value": to_espo_time(modified_since)})
        for row in self.espo.iterate("Opportunity", where, DEAL_FIELDS):
            yield to_deal(row)

    def deals(self) -> Iterator[CrmDeal]:
        for row in self.espo.iterate("Opportunity", [], DEAL_FIELDS):
            yield to_deal(row)

    def deal(self, deal_id: str) -> CrmDeal:
        if not deal_id.isalnum():
            raise PermanentError(f"not an EspoCRM id: {deal_id!r}")
        return to_deal(self.espo.get("Opportunity", deal_id))

    def account(self, account_id: str) -> CrmAccount:
        row = self.espo.get("Account", account_id)
        return CrmAccount(row["id"], row["name"], row.get("website") or None, row.get("billingAddressCountry") or None)

    def write_back(self, deal_id: str, invoice_ref: str | None, status: BillingStatus) -> None:
        self.espo.update("Opportunity", deal_id, {"cErpInvoiceRef": invoice_ref or "", "cBillingStatus": status.value})
