"""Tracing stays off without Langfuse keys, the sender is never sent in clear, and the metric names the alert rules
and the dashboard use are the ones the hub exports."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hub import metrics, tracing
from hub.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_tracing_is_off_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(tracing, "_client", None)
    assert tracing.setup(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-generate-me")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-generate-me")
    assert tracing.setup(Settings(_env_file=None)) is None, "placeholder keys from .env.example must not enable it"  # type: ignore[call-arg]


def test_sender_is_hashed() -> None:
    a = tracing.sender_id("Hannah.Brooks@Example.com")
    assert a == tracing.sender_id("hannah.brooks@example.com") and "hannah" not in a and a.startswith("sender-")


def test_every_metric_in_alerts_and_dashboard_exists() -> None:
    exported = {
        m._name
        for m in (
            metrics.SYNC_RUNS,
            metrics.SYNC_DEALS,
            metrics.SYNC_SECONDS,
            metrics.SYNC_LAST_SUCCESS,
            metrics.DEAD_LETTERS_OPEN,
            metrics.RECON_EXCEPTIONS,
            metrics.RECON_LAST_RUN,
            metrics.TICKETS,
            metrics.WEBHOOKS,
            metrics.TRIAGE,
            metrics.TRIAGE_SECONDS,
            metrics.HTTP_SECONDS,
        )
    }
    # prometheus_client strips _total from counter names; the exposition adds _total, _bucket, _count back
    names = (
        exported
        | {f"{n}_total" for n in exported}
        | {f"{n}_bucket" for n in exported}
        | {f"{n}_count" for n in exported}
    )
    text = (ROOT / "observability" / "alert_rules.yml").read_text() + (
        ROOT / "observability" / "grafana" / "dashboards" / "finops-hub.json"
    ).read_text()
    used = set(re.findall(r"\bhub_[a-z_]+", text))
    assert used <= names, f"unknown metrics: {sorted(used - names)}"


def test_multiprocess_exposition_combines_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    script = "from hub import metrics; metrics.SYNC_RUNS.labels('succeeded').inc()"
    for _ in range(2):  # two "workers", each its own process writing to the shared directory
        subprocess.run([sys.executable, "-c", script], check=True, env={**__import__("os").environ})
    text = metrics.exposition().decode()
    assert 'hub_sync_runs_total{status="succeeded"} 2.0' in text
