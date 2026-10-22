"""Send the challenge-set e-mails through the n8n intake webhook (n8n -> hub -> triage agent with the real model),
then read every turn back from Langfuse's Observations API v2: latency, LLM calls and token counts per turn, as
Langfuse recorded them (not as the client measured them).

    uv run python scripts/langfuse_report.py --out reports/triage/langfuse-run.json

Needs the full stack with the llm profile and a real model on the running hub (HUB_TRIAGE_MODEL). Langfuse v4 has
no /traces endpoint; a trace is the set of observations sharing a traceId, its root is the `triage` agent span.
OpenInference records each LLM call twice (`Ollama.acomplete` wraps `Ollama.achat`); only `acomplete` is counted.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

N8N = "http://127.0.0.1:5678"
LANGFUSE = os.environ.get("LANGFUSE_HOST_URL", "http://127.0.0.1:3000")


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


def observations(client: httpx.Client, since: datetime, until: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        params = {
            "fromStartTime": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "toStartTime": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fields": "core,basic,model,usage,metrics",
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/public/v2/observations", params=params).json()
        out += body.get("data", [])
        cursor = (body.get("meta") or {}).get("cursor")
        if not cursor:
            return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("reports/triage/langfuse-run.json"))
    ap.add_argument("--limit", type=int, default=90)
    a = ap.parse_args()
    from hub.triage.eval import challenge_set

    cases = challenge_set()[: a.limit]
    auth = (os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])
    started = datetime.now(UTC) - timedelta(seconds=5)
    answers = []
    for c in cases:
        r = httpx.post(
            f"{N8N}/webhook/billing-message",
            json={"message": c.text, "sender": f"{c.id}@customers.example"},
            headers={"X-Webhook-Key": os.environ["N8N_WEBHOOK_KEY"]},
            timeout=300,
        )
        answers.append({"id": c.id, "status": r.status_code, **(r.json() if r.status_code == 202 else {})})
    time.sleep(30)  # the SDK exports in batches; Langfuse's worker writes to ClickHouse asynchronously

    with httpx.Client(base_url=LANGFUSE, auth=auth, timeout=120) as lf:
        obs = observations(lf, started, datetime.now(UTC) + timedelta(minutes=5))
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in obs:
        by_trace[o["traceId"]].append(o)
    turns = []
    for trace_id, items in by_trace.items():
        root = next((o for o in items if o.get("type") == "AGENT" and o.get("name") == "triage"), None)
        if root is None:
            continue
        calls = [o for o in items if o.get("type") == "GENERATION" and str(o.get("name", "")).endswith(".acomplete")]
        turns.append(
            {
                "trace": trace_id,
                "latency_s": root.get("latency"),
                "observations": len(items),
                "llm_calls": len(calls),
                "prompt_tokens": sum(int(o.get("inputUsage") or 0) for o in calls),
                "completion_tokens": sum(int(o.get("outputUsage") or 0) for o in calls),
                "llm_seconds": round(sum(float(o.get("latency") or 0) for o in calls), 3),
                "models": sorted({o.get("model") or "" for o in calls} - {""}),
            }
        )
    lat = [t["latency_s"] for t in turns if t["latency_s"] is not None]
    report = {
        "what": "challenge-set e-mails through n8n -> hub -> triage agent, every turn read back from Langfuse",
        "setup": "local stack on one machine; qwen2.5:7b in Ollama on an RTX 4060 8 GB; Langfuse 4.67 self-hosted",
        "sent": len(cases),
        "accepted": sum(1 for x in answers if x["status"] == 202),
        "traces_in_langfuse": len(turns),
        "observations_in_langfuse": len(obs),
        "latency_s_p50": pct(lat, 0.5) if lat else None,
        "latency_s_p95": pct(lat, 0.95) if lat else None,
        "llm_calls_per_turn_mean": round(statistics.mean(t["llm_calls"] for t in turns), 2) if turns else None,
        "prompt_tokens_per_turn_mean": round(statistics.mean(t["prompt_tokens"] for t in turns), 1) if turns else None,
        "completion_tokens_per_turn_mean": round(statistics.mean(t["completion_tokens"] for t in turns), 1)
        if turns
        else None,
        "llm_share_of_latency": round(sum(t["llm_seconds"] for t in turns) / sum(lat), 3) if lat else None,
        "models": sorted({m for t in turns for m in t["models"]}),
        "turns": sorted(turns, key=lambda t: t["trace"]),
        "answers": answers,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("turns", "answers")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
