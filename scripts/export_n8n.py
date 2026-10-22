"""Export the workflows from the running n8n into n8n/workflows, keeping only what describes the workflow.

    uv run python scripts/export_n8n.py          (needs `docker compose --profile automation up`)

n8n's export carries instance state (timestamps, the owning project, version counters, static data); those are
dropped so the committed files change only when a workflow does, and re-import on a fresh instance works.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

KEEP = ("id", "name", "active", "settings", "nodes", "connections", "pinData", "tags")
OUT = Path(__file__).resolve().parents[1] / "n8n" / "workflows"
TMP = "/tmp/wf"  # noqa: S108  (a path inside the n8n container, not on this machine)


def compose(*args: str) -> str:
    profiles = [a for p in ("core", "tickets", "automation", "llm", "obs") for a in ("--profile", p)]
    out = subprocess.run(
        ["docker", "compose", *profiles, *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return out.stdout


def main() -> int:
    compose(
        "exec",
        "-T",
        "n8n",
        "sh",
        "-c",
        f"rm -rf {TMP} && mkdir -p {TMP} && n8n export:workflow --all --separate --output={TMP}/",
    )
    names = [n.strip() for n in compose("exec", "-T", "n8n", "ls", TMP).splitlines() if n.strip().endswith(".json")]
    for name in names:
        wf = json.loads(compose("exec", "-T", "n8n", "cat", f"{TMP}/{name}"))
        clean = {k: wf[k] for k in KEEP if k in wf}
        clean["tags"] = [t["name"] if isinstance(t, dict) else t for t in clean.get("tags", [])]
        (OUT / name).write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"exported {name}: {len(clean['nodes'])} nodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
