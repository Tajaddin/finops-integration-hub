"""Drive the n8n workflows with a batch of traffic and report run counts and durations from n8n's own execution
table (not from the client's clock).

    uv run python scripts/measure_n8n.py --messages 100 --out reports/n8n/runs.json

Messages are Bitext test-split messages (never trained on) plus refund requests with amounts on both sides of the
approval threshold; every approval e-mail is answered (approve or reject, alternating). Which model triaged them is
recorded in the report: HUB_TRIAGE_MODEL of the running hub.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import time
from pathlib import Path

import httpx
import psycopg

N8N, MAILPIT, HUB = "http://127.0.0.1:5678", "http://127.0.0.1:8025", "http://127.0.0.1:8000"


def post(path: str, key: str, body: dict[str, object] | None = None) -> httpx.Response:
    return httpx.post(f"{N8N}/webhook/{path}", json=body or {}, headers={"X-Webhook-Key": key}, timeout=600)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--messages", type=int, default=100)
    ap.add_argument("--out", type=Path, default=Path("reports/n8n/runs.json"))
    a = ap.parse_args()
    key = os.environ["N8N_WEBHOOK_KEY"]

    from hub.triage.data import messages, split

    _, test = split(messages())
    rng = random.Random(11)  # noqa: S311
    batch = [m.text for m in rng.sample(test, a.messages)]
    batch += [
        f"I want a refund of ${amount:,.2f} for invoice INV/2026/{100 + i:05d}"
        for i, amount in enumerate([120, 480, 650, 1250, 4800, 90, 2400, 300, 700, 5000])
    ]
    with psycopg.connect(os.environ["HUB_DATABASE_URL"]) as conn:
        first_run = conn.execute("SELECT coalesce(max(id), 0) FROM triage_run").fetchone()[0]  # type: ignore[index]
    seen_mail = {m["ID"] for m in httpx.get(f"{MAILPIT}/api/v1/messages", params={"limit": 500}).json()["messages"]}
    started = time.time()
    statuses: dict[int, int] = {}
    for text in batch:
        r = post("billing-message", key, {"message": text, "sender": "measure@example.com"})
        statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
    for _ in range(3):
        post("run-sync", key)
    for _ in range(2):
        post("run-reconciliation", key)
    time.sleep(5)
    approvals = 0
    for m in httpx.get(f"{MAILPIT}/api/v1/messages", params={"limit": 500}).json()["messages"]:
        if m["ID"] in seen_mail or not m["Subject"].startswith("Refund approval needed"):
            continue
        text = httpx.get(f"{MAILPIT}/api/v1/message/{m['ID']}").json()["Text"]
        decision = "approved" if approvals % 2 == 0 else "rejected"
        link = re.search(rf"(http\S+decision={decision})", text)
        if link and httpx.get(link.group(1), timeout=30).status_code == 200:
            approvals += 1
    time.sleep(5)
    elapsed = time.time() - started

    n8n_dsn = f"postgresql://n8n:{os.environ['N8N_DB_PASSWORD']}@127.0.0.1:55432/n8n"
    with psycopg.connect(n8n_dsn) as conn:
        rows = conn.execute(
            """SELECT w.name, e.status, e.mode, extract(epoch FROM (e."stoppedAt" - e."startedAt")) * 1000
               FROM execution_entity e JOIN workflow_entity w ON w.id = e."workflowId"
               WHERE e."startedAt" >= to_timestamp(%s)""",
            (started - 1,),
        ).fetchall()
    with psycopg.connect(os.environ["HUB_DATABASE_URL"]) as conn:
        decided = conn.execute(
            "SELECT decision, count(*) FROM triage_run WHERE id > %s GROUP BY decision ORDER BY decision NULLS FIRST",
            (first_run,),
        ).fetchall()
    per: dict[str, dict[str, object]] = {}
    for name, status, _mode, ms in rows:
        w = per.setdefault(name, {"runs": 0, "by_status": {}, "_ms": []})
        w["runs"] = int(w["runs"]) + 1  # type: ignore[call-overload]
        w["by_status"][status] = w["by_status"].get(status, 0) + 1  # type: ignore[index, union-attr]
        if ms is not None and status == "success":
            w["_ms"].append(float(ms))  # type: ignore[union-attr]
    for w in per.values():
        ms_list = sorted(w.pop("_ms"))  # type: ignore[arg-type]
        if ms_list:
            w["ms_p50"] = round(statistics.median(ms_list), 1)
            w["ms_p95"] = round(ms_list[min(len(ms_list) - 1, int(0.95 * len(ms_list)))], 1)
    report = {
        "what": "n8n workflows driven through their webhooks; counts and durations from n8n's execution_entity",
        "setup": "local Docker stack on one machine, n8n 2.45.1, Postgres-backed",
        "triage_model": os.environ.get("HUB_TRIAGE_MODEL", "?"),
        "billing_messages_sent": len(batch),
        "webhook_status_codes": statuses,
        "approval_links_followed": approvals,
        "triage_decisions": {str(k): v for k, v in decided},
        "seconds": round(elapsed, 1),
        "workflows": per,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
