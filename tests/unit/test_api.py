"""The API with fake systems: who may call what, and what a CRM webhook must prove before it is believed."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from hub.api.app import Deps, create_app
from hub.api.security import WebhookSecret, espocrm_signature, verify_espocrm
from hub.config import Settings
from hub.store import Store
from tests.fakes import FakeCrm, FakeErp, make_deal

KEY = "local-dev-key-0000000000000000"


@pytest.fixture
def setup(store: Store, tmp_path: Path) -> tuple[TestClient, FakeCrm, FakeErp]:
    secret = tmp_path / "hook.json"
    secret.write_text(json.dumps({"id": "hook1", "secret": "s3cret"}))
    settings = Settings(api_key=SecretStr(KEY), espocrm_webhook_secret_file=secret, crm="espocrm", _env_file=None)  # type: ignore[call-arg]
    crm, erp = FakeCrm([make_deal(i) for i in range(3)]), FakeErp()
    app = create_app(settings, Deps(crm=lambda: crm, erp=lambda: erp, store=store, tickets=lambda: None))
    return TestClient(app), crm, erp


def test_health_and_ready_need_no_key(setup: tuple[TestClient, FakeCrm, FakeErp]) -> None:
    client, _, _ = setup
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    assert b"hub_sync_runs_total" in client.get("/metrics").content


def test_sync_needs_the_api_key(setup: tuple[TestClient, FakeCrm, FakeErp]) -> None:
    client, _, erp = setup
    assert client.post("/v1/sync/runs").status_code == 401
    assert client.post("/v1/sync/runs", headers={"X-API-Key": "wrong"}).status_code == 401
    r = client.post("/v1/sync/runs", headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.json()["created"] == 3 and len(erp.invoice_rows) == 3


def test_api_refuses_everything_without_a_configured_key(store: Store) -> None:
    settings = Settings(api_key=SecretStr(""), _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings, Deps(crm=lambda: FakeCrm([]), erp=FakeErp, store=store, tickets=lambda: None))
    assert TestClient(app).post("/v1/sync/runs", headers={"X-API-Key": ""}).status_code == 503


def signed(body: bytes, secret: str = "s3cret", hook: str = "hook1") -> dict[str, str]:
    return {"Signature": espocrm_signature(hook, secret, body), "Content-Type": "application/json"}


def test_signed_webhook_syncs_the_named_deals(setup: tuple[TestClient, FakeCrm, FakeErp]) -> None:
    client, _, erp = setup
    body = json.dumps([{"id": "deal1", "stage": "Closed Won"}]).encode()
    r = client.post("/webhooks/espocrm", content=body, headers=signed(body))
    assert r.status_code == 202 and r.json() == {"accepted": 1}
    deadline = time.time() + 5  # TestClient runs background tasks before returning, but be tolerant
    while not erp.invoice_rows and time.time() < deadline:
        time.sleep(0.05)
    assert [i.origin for i in erp.invoice_rows.values()] == ["fakecrm:deal1"]


@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), ({"Signature": "not-base64!"}, 401), ("wrong-secret", 401), ("wrong-hook", 401)],
)
def test_unsigned_or_wrongly_signed_webhooks_are_rejected(
    setup: tuple[TestClient, FakeCrm, FakeErp], headers: object, status: int
) -> None:
    client, _, erp = setup
    body = json.dumps([{"id": "deal1"}]).encode()
    if headers == "wrong-secret":
        headers = signed(body, secret="guess")
    elif headers == "wrong-hook":
        headers = signed(body, hook="other")
    assert client.post("/webhooks/espocrm", content=body, headers=headers).status_code == status  # type: ignore[arg-type]
    assert erp.invoice_rows == {}


def test_replayed_and_malformed_webhooks(setup: tuple[TestClient, FakeCrm, FakeErp]) -> None:
    client, _, _ = setup
    body = json.dumps([{"id": "deal2"}]).encode()
    assert client.post("/webhooks/espocrm", content=body, headers=signed(body)).json() == {"accepted": 1}
    assert client.post("/webhooks/espocrm", content=body, headers=signed(body)).json() == {
        "accepted": 0,
        "replay": True,
    }
    bad = json.dumps([{"id": "../../etc"}]).encode()
    assert client.post("/webhooks/espocrm", content=bad, headers=signed(bad)).status_code == 422
    big = b"[" + b'{"id":"a"},' * 100_000 + b'{"id":"a"}]'
    assert client.post("/webhooks/espocrm", content=big, headers=signed(big)).status_code == 413


def test_signature_matches_espocrms_php_example() -> None:
    # computed with PHP in the EspoCRM container: base64_encode("6ad9:" . hash_hmac("sha256", "[]", "key"))
    sig = espocrm_signature("6ad9", "key", b"[]")
    assert sig == "NmFkOTo4Y2YzZDU4NGJhYzQyMDcwZTQ0ZjI0OWE1NTA5MTBiZDAwNzdkMThhOWNjNjJhNTRjNTljNjc1ODQ1OGJmMDFh"
    assert verify_espocrm(sig, b"[]", WebhookSecret("6ad9", "key"))
    assert not verify_espocrm(sig, b"[ ]", WebhookSecret("6ad9", "key"))


class FakeTriage:
    """Stands in for the TriageWorkflow: counts runs, answers a held refund."""

    def __init__(self) -> None:
        self.runs = 0

    async def run(self, message: str) -> object:
        from hub.triage.workflow import TriageResult

        self.runs += 1
        return TriageResult(
            "get_refund", "REFUND", "Billing", "high", "baseline", 0.99, 4800.0, True, reply="Passed to finance."
        )


def test_triage_is_idempotent_per_key_and_decisions_are_recorded_once(store: Store, tmp_path: Path) -> None:
    settings = Settings(api_key=SecretStr(KEY), _env_file=None)  # type: ignore[call-arg]
    fake = FakeTriage()
    app = create_app(
        settings, Deps(crm=lambda: FakeCrm([]), erp=FakeErp, store=store, tickets=lambda: None, triage=lambda: fake)
    )
    client = TestClient(app)
    h = {"X-API-Key": KEY, "Idempotency-Key": "n8n-42"}
    body = {"message": "refund $4,800.00 please", "sender": "a@example.com"}
    first = client.post("/v1/triage", json=body, headers=h).json()
    again = client.post("/v1/triage", json=body, headers=h).json()
    assert fake.runs == 1, "a retry with the same key must not triage (or file a ticket) twice"
    assert again["replay"] is True and again["id"] == first["id"] and first["needs_approval"] is True
    assert (
        client.post(
            "/v1/triage", json={"message": "x", "sender": "not-an-email"}, headers={"X-API-Key": KEY}
        ).status_code
        == 422
    )
    decide = {"approved": True, "by": "controller"}
    assert (
        client.post(f"/v1/triage/{first['id']}/decision", json=decide, headers={"X-API-Key": KEY}).json()["decision"]
        == "approved"
    )
    assert client.post(f"/v1/triage/{first['id']}/decision", json=decide, headers={"X-API-Key": KEY}).status_code == 409
