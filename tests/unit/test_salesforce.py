"""SalesforceCrm against the request and response shapes in Salesforce's REST API Developer Guide (OAuth 2.0 client
credentials flow, query with nextRecordsUrl, sObject PATCH returning 204). Not a substitute for a live org; the
live run is backlog issue #19."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from hub.connectors.base import PermanentError
from hub.connectors.salesforce import SalesforceCrm
from hub.domain import BillingStatus, Plan, Term

TOKEN = {
    "access_token": "00Dxx!AQ0AQ",
    "instance_url": "https://acme.my.salesforce.com",
    "token_type": "Bearer",
    "issued_at": "1760000000000",
    "signature": "sig",
}


def opp(i: int) -> dict[str, object]:
    return {
        "attributes": {
            "type": "Opportunity",
            "url": f"/services/data/v66.0/sobjects/Opportunity/006xx00000000{i:02d}AAA",
        },
        "Id": f"006xx00000000{i:02d}AAA",
        "AccountId": "001xx000003DGb2AAG",
        "Name": f"Deal {i}",
        "StageName": "Closed Won",
        "IsWon": True,
        "Amount": 3442.5,
        "CloseDate": "2026-09-01",
        "Plan__c": "Team",
        "Seats__c": 25.0,
        "Term__c": "Annual",
        "Discount_Pct__c": 10.0,
        "ERP_Invoice_Ref__c": None,
        "Billing_Status__c": None,
        "SystemModstamp": "2026-10-21T18:04:11.000+0000",
    }


class FakeOrg:
    def __init__(self, expire_first_query: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.expire = expire_first_query

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/services/oauth2/token":
            self.tokens += 1
            return httpx.Response(200, json=TOKEN)
        if path.endswith("/query"):
            if self.expire:
                self.expire = False
                return httpx.Response(
                    401, json=[{"message": "Session expired or invalid", "errorCode": "INVALID_SESSION_ID"}]
                )
            return httpx.Response(
                200,
                json={
                    "totalSize": 3,
                    "done": False,
                    "nextRecordsUrl": "/services/data/v66.0/query/01gxx-2000",
                    "records": [opp(1), opp(2)],
                },
            )
        if path.endswith("/query/01gxx-2000"):
            return httpx.Response(200, json={"totalSize": 3, "done": True, "records": [opp(3)]})
        if "/sobjects/Opportunity/" in path and request.method == "PATCH":
            return httpx.Response(204)
        if "/sobjects/Account/" in path:
            return httpx.Response(
                200,
                json={
                    "attributes": {"type": "Account"},
                    "Id": "001xx000003DGb2AAG",
                    "Name": "Acme",
                    "Website": "acme.com",
                    "BillingCountry": "US",
                },
            )
        return httpx.Response(404, json=[{"message": "not found", "errorCode": "NOT_FOUND"}])


def crm(org: FakeOrg) -> SalesforceCrm:
    return SalesforceCrm(
        "https://acme.my.salesforce.com", "client-id", "client-secret", transport=httpx.MockTransport(org), retries=0
    )


def test_client_credentials_then_soql_with_paging() -> None:
    org = FakeOrg()
    deals = list(crm(org).won_deals(datetime(2026, 10, 1, tzinfo=UTC)))
    assert [d.seats for d in deals] == [25, 25, 25] and len(deals) == 3
    token_req = org.requests[0]
    assert parse_qs(token_req.content.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
    }
    soql = org.requests[1].url.params["q"]
    assert (
        "FROM Opportunity WHERE IsWon = true AND SystemModstamp > 2026-10-01T00:00:00Z ORDER BY SystemModstamp ASC"
        in soql
    )
    assert org.requests[1].headers["authorization"] == "Bearer 00Dxx!AQ0AQ"
    d = deals[0]
    assert (d.plan, d.term, d.discount_pct, d.stage, d.currency) == (Plan.TEAM, Term.ANNUAL, 10, "Closed Won", "USD")


def test_expired_session_gets_one_new_token() -> None:
    org = FakeOrg(expire_first_query=True)
    assert len(list(crm(org).won_deals(None))) == 3
    assert org.tokens == 2


def test_write_back_patches_the_custom_fields() -> None:
    org = FakeOrg()
    crm(org).write_back("006xx0000000001AAA", "INV/2026/00041", BillingStatus.INVOICED)
    patch = org.requests[-1]
    assert patch.method == "PATCH" and patch.url.path == "/services/data/v66.0/sobjects/Opportunity/006xx0000000001AAA"
    assert json.loads(patch.content) == {"ERP_Invoice_Ref__c": "INV/2026/00041", "Billing_Status__c": "invoiced"}


def test_account_ids_are_validated_before_they_reach_a_url() -> None:
    with pytest.raises(PermanentError):
        crm(FakeOrg()).account("../../limits")
    assert crm(FakeOrg()).account("001xx000003DGb2AAG").website == "acme.com"
