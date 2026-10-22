"""Keep the GitHub Projects board in step with the milestones and the sprint's progress.

    python scripts/agile/board.py sprints              set every card's Sprint field from its issue's milestone
    python scripts/agile/board.py status 4 6 "In Progress"
                                                      set the Status of issues #4 and #6
    python scripts/agile/board.py status --sprint "Sprint 1 - Systems and sync" Done

Everything goes through the `gh` CLI (needs the project scope); nothing here holds a token.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any


def gh(*args: str) -> str:
    out = subprocess.run(["gh", *args], check=False, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {out.stderr.strip()}")
    return out.stdout


def board() -> tuple[str, str, str, dict[str, Any]]:
    repo = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
    owner, name = repo.split("/")
    projects = json.loads(gh("project", "list", "--owner", owner, "--format", "json"))["projects"]
    project = next(p for p in projects if p["title"] == f"{name} sprints")
    number = str(project["number"])
    fields = {
        f["name"]: f
        for f in json.loads(gh("project", "field-list", number, "--owner", owner, "--format", "json"))["fields"]
    }
    return owner, number, project["id"], fields


def items(owner: str, number: str) -> list[dict[str, Any]]:
    data = json.loads(gh("project", "item-list", number, "--owner", owner, "--format", "json", "--limit", "500"))
    out: list[dict[str, Any]] = data["items"]
    return out


def set_option(project_id: str, item_id: str, field: dict[str, Any], option_name: str) -> None:
    option = next(o for o in field["options"] if o["name"] == option_name)
    gh(
        "project",
        "item-edit",
        "--id",
        item_id,
        "--project-id",
        project_id,
        "--field-id",
        field["id"],
        "--single-select-option-id",
        option["id"],
    )


def milestones() -> dict[int, str]:
    issues = json.loads(gh("issue", "list", "--state", "all", "--limit", "500", "--json", "number,milestone"))
    return {i["number"]: (i["milestone"] or {}).get("title", "") for i in issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sprints")
    st = sub.add_parser("status")
    st.add_argument("args", nargs="+", help="issue numbers followed by the status name")
    st.add_argument("--sprint", help="every issue in this milestone instead of numbers")
    a = parser.parse_args()

    owner, number, project_id, fields = board()
    by_issue = {i["content"]["number"]: i for i in items(owner, number) if i.get("content", {}).get("number")}
    ms = milestones()
    if a.cmd == "sprints":
        for n, item in sorted(by_issue.items()):
            if ms.get(n) and ms[n] != "Backlog":
                set_option(project_id, item["id"], fields["Sprint"], ms[n])
        print(f"Sprint set on {len(by_issue)} cards")
        return 0
    status = a.args[-1]
    targets = [n for n, m in ms.items() if m == a.sprint] if a.sprint else [int(x) for x in a.args[:-1]]
    for n in targets:
        set_option(project_id, by_issue[n]["id"], fields["Status"], status)
    print(f"Status {status!r} on #{', #'.join(str(n) for n in sorted(targets))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
