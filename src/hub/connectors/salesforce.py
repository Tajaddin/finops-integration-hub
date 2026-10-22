"""Salesforce REST API as a CrmSource: OAuth 2.0 client credentials flow (an External Client App with a run-as
integration user), SOQL with `nextRecordsUrl` paging, and a PATCH of two custom fields for the write-back.

The custom fields (`Plan__c`, `Seats__c`, `Term__c`, `Discount_Pct__c`, `ERP_Invoice_Ref__c`, `Billing_Status__c`)
are in `salesforce/` as source-format metadata. Not yet run against a live org (needs an account; see
docs/DEPLOY.md); tests/unit/test_salesforce.py checks it against the response shapes in Salesforce's REST docs.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx

from hub.connectors.base import PermanentError
from hub.connectors.http import ApiError, Http
from hub.domain import BillingStatus, CrmAccount, CrmDeal, Plan, Term

FIELDS = (
    "Id, AccountId, Name, StageName, IsWon, Amount, CloseDate, Plan__c, Seats__c, Term__c, Discount_Pct__c, "
    "ERP_Invoice_Ref__c, Billing_Status__c, SystemModstamp"
)


def soql_datetime(value: datetime) -> str:
    """SOQL datetime literals are unquoted ISO 8601 in UTC."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sf_time(value: str) -> datetime:
    # e.g. 2026-10-21T18:04:11.000+0000
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


class SalesforceCrm:
    name = "salesforce"

    def __init__(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        *,
        api_version: str = "v66.0",
        currency: str = "USD",
        multicurrency: bool = False,
        timeout: float = 20.0,
        retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base = f"/services/data/{api_version}"
        self._currency = currency
        self._multicurrency = multicurrency
        self.http = Http(
            "salesforce",
            instance_url.rstrip("/"),
            {"Accept": "application/json"},
            timeout,
            retries,
            transport=transport,
        )
        self._token: str | None = None

    def _authenticate(self) -> None:
        response = self.http.request(
            "POST",
            "/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            idempotent=True,  # a token request creates nothing
        )
        body = response.json()
        self._token = body["access_token"]
        self.http.client.headers["Authorization"] = f"Bearer {self._token}"

    def _call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._token is None:
            self._authenticate()
        try:
            return self.http.request(method, url, **kwargs)
        except ApiError as exc:
            if exc.status != 401:  # session expired or revoked: one fresh token, then give up
                raise
            self._authenticate()
            return self.http.request(method, url, **kwargs)

    def query(self, soql: str) -> Iterator[dict[str, Any]]:
        url: str | None = f"{self._base}/query"
        params: dict[str, str] | None = {"q": soql}
        while url:
            body = self._call("GET", url, params=params).json()
            yield from body.get("records", [])
            url = None if body.get("done", True) else body.get("nextRecordsUrl")
            params = None

    def _to_deal(self, r: dict[str, Any]) -> CrmDeal:
        def enum[E: (Plan, Term, BillingStatus)](cls: type[E], value: Any) -> E | None:
            try:
                return cls(str(value).lower()) if value else None
            except ValueError as exc:
                raise PermanentError(f"opportunity {r['Id']}: {value!r} is not a valid {cls.__name__}") from exc

        return CrmDeal(
            id=r["Id"],
            account_id=r.get("AccountId") or "",
            name=r.get("Name") or "",
            stage="Closed Won" if r.get("IsWon") else (r.get("StageName") or ""),
            amount=Decimal(str(r.get("Amount") or 0)).quantize(Decimal("0.01")),
            currency=r.get("CurrencyIsoCode") or self._currency,
            close_date=date.fromisoformat(r["CloseDate"]) if r.get("CloseDate") else None,
            plan=enum(Plan, r.get("Plan__c")),
            seats=int(r["Seats__c"]) if r.get("Seats__c") is not None else None,
            term=enum(Term, r.get("Term__c")),
            discount_pct=int(r.get("Discount_Pct__c") or 0),
            modified_at=sf_time(r["SystemModstamp"]),
            erp_invoice_ref=r.get("ERP_Invoice_Ref__c") or None,
            billing_status=enum(BillingStatus, r.get("Billing_Status__c")),
        )

    def _select(self) -> str:
        return FIELDS + (", CurrencyIsoCode" if self._multicurrency else "")

    def won_deals(self, modified_since: datetime | None) -> Iterator[CrmDeal]:
        where = "IsWon = true"
        if modified_since is not None:
            where += f" AND SystemModstamp > {soql_datetime(modified_since)}"
        soql = f"SELECT {self._select()} FROM Opportunity WHERE {where} ORDER BY SystemModstamp ASC"
        for r in self.query(soql):
            yield self._to_deal(r)

    def deals(self) -> Iterator[CrmDeal]:
        for r in self.query(f"SELECT {self._select()} FROM Opportunity ORDER BY SystemModstamp ASC"):
            yield self._to_deal(r)

    def deal(self, deal_id: str) -> CrmDeal:
        if not deal_id.isalnum() or len(deal_id) not in (15, 18):
            raise PermanentError(f"not a Salesforce id: {deal_id!r}")
        rows = list(self.query(f"SELECT {self._select()} FROM Opportunity WHERE Id = '{deal_id}'"))
        if not rows:
            raise PermanentError(f"opportunity {deal_id} not found")
        return self._to_deal(rows[0])

    def account(self, account_id: str) -> CrmAccount:
        if not account_id.isalnum() or len(account_id) not in (15, 18):
            raise PermanentError(f"not a Salesforce id: {account_id!r}")
        r = self._call(
            "GET", f"{self._base}/sobjects/Account/{account_id}", params={"fields": "Id,Name,Website,BillingCountry"}
        ).json()
        return CrmAccount(r["Id"], r["Name"], r.get("Website") or None, r.get("BillingCountry") or None)

    def write_back(self, deal_id: str, invoice_ref: str | None, status: BillingStatus) -> None:
        self._call(
            "PATCH",
            f"{self._base}/sobjects/Opportunity/{deal_id}",
            json={"ERP_Invoice_Ref__c": invoice_ref, "Billing_Status__c": status.value},
            idempotent=True,  # setting two fields to fixed values
        )
