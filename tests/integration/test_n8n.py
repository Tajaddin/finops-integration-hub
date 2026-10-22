"""The three n8n workflows against the running stack (`make up` with the automation profile): each webhook path
needs the webhook key, the sync and the reconciliation report back, and a large refund goes through the approval
e-mail and its signed resume link into the hub's record. The triage model can be the CI stand-in
(HUB_TRIAGE_MODEL=fake): this tests the wiring, reports/triage/ measures the model."""

from __future__ import annotations

import os
import re
import time

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.integration
N8N = "http://127.0.0.1:5678"
MAILPIT = "http://127.0.0.1:8025"


@pytest.fixture(scope="module")
def key() -> str:
    value = os.environ.get("N8N_WEBHOOK_KEY")
    try:
        httpx.get(f"{N8N}/healthz", timeout=3).raise_for_status()
    except (httpx.HTTPError, OSError):
        pytest.skip("n8n is not running (docker compose --profile automation up)")
    if not value:
        pytest.skip("N8N_WEBHOOK_KEY is not set (.env)")
    return value


def post(path: str, key: str | None, body: dict[str, object] | None = None, timeout: float = 600) -> httpx.Response:
    headers = {"X-Webhook-Key": key} if key else {}
    for _ in range(30):  # right after a restart n8n answers 404 until it has registered its webhooks
        r = httpx.post(f"{N8N}/webhook/{path}", json=body or {}, headers=headers, timeout=timeout)
        if r.status_code != 404:
            return r
        time.sleep(2)
    return r


def mail_ids() -> set[str]:
    return {
        m["ID"] for m in httpx.get(f"{MAILPIT}/api/v1/messages", params={"limit": 50}, timeout=10).json()["messages"]
    }


def new_mail(subject_prefix: str, before: set[str]) -> dict[str, object]:
    """The first e-mail with this subject that was not in Mailpit before the action."""
    for _ in range(30):
        for m in httpx.get(f"{MAILPIT}/api/v1/messages", params={"limit": 50}, timeout=10).json()["messages"]:
            if m["ID"] not in before and m["Subject"].startswith(subject_prefix):
                return httpx.get(f"{MAILPIT}/api/v1/message/{m['ID']}", timeout=10).json()
        time.sleep(1)
    raise AssertionError(f"no new e-mail starting with {subject_prefix!r}")


def test_webhooks_refuse_callers_without_the_key(key: str) -> None:
    assert post("run-sync", None).status_code == 403
    assert post("run-sync", "wrong").status_code == 403


def test_sync_workflow_returns_the_hub_result(key: str) -> None:
    r = post("run-sync", key)
    assert r.status_code == 200, r.text
    assert {"seen", "created", "dead_lettered"} <= set(r.json())


def test_reconciliation_workflow_mails_finance_ops(key: str) -> None:
    before = mail_ids()
    assert post("run-reconciliation", key).status_code == 200
    mail = new_mail("Reconciliation:", before)
    assert "Open exceptions by class:" in str(mail["Text"])


def test_large_refund_waits_for_approval_and_the_decision_is_recorded(key: str) -> None:
    before = mail_ids()
    r = post(
        "billing-message",
        key,
        {"message": "I want a refund of $4,800.00 for invoice INV/2026/00190", "sender": "ci@example.com"},
    )
    assert r.status_code == 202, r.text
    triage = r.json()
    assert triage["intent"] == "get_refund" and triage["needs_approval"] is True and triage["ticket"]
    mail = new_mail("Refund approval needed", before)
    approve = re.search(r"(http\S+decision=approved)", str(mail["Text"]))
    assert approve, mail["Text"]
    assert httpx.get(approve.group(1), timeout=30).status_code == 200
    with psycopg.connect(os.environ["HUB_DATABASE_URL"]) as conn:
        for _ in range(20):
            row = conn.execute("SELECT decision, decided_by FROM triage_run WHERE id = %s", (triage["id"],)).fetchone()
            if row and row[0]:
                break
            time.sleep(1)
    assert row == ("approved", "finance (n8n approval link)")


def test_small_refund_is_not_held(key: str) -> None:
    r = post(
        "billing-message",
        key,
        {"message": "I want a refund of $120.00 for invoice INV/2026/00101", "sender": "ci@example.com"},
    )
    assert r.status_code == 202
    assert r.json()["needs_approval"] is False
