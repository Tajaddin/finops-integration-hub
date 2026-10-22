"""Write .env from .env.example, replacing every generate-me placeholder with a fresh random value.

Refuses to overwrite an existing .env (the databases were initialised with its passwords); pass --force to rotate
everything, which also means `make down` to drop the volumes.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fill(line: str) -> str:
    def repl(m: re.Match[str]) -> str:
        prefix, kind = m.group(1) or "", m.group(2)
        if kind == "-64":  # Langfuse wants exactly 64 hex characters (256 bits)
            return prefix + secrets.token_hex(32)
        return prefix + secrets.token_hex(24)

    return re.sub(r"((?:pk|sk)-lf-)?generate-me(-64)?", repl, line)


def main() -> int:
    target = ROOT / ".env"
    if target.exists() and "--force" not in sys.argv:
        print(".env exists; keep it (the databases use its passwords) or pass --force")
        return 0
    lines = [fill(line) for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()]
    # expand ${NAME} references to values defined earlier in the file (e.g. the hub's database URL)
    values: dict[str, str] = {}
    out = []
    for raw in lines:
        line = raw
        if "=" in raw and not raw.lstrip().startswith("#"):
            key, value = raw.split("=", 1)
            value = re.sub(r"\$\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), value)
            values[key.strip()] = value
            line = f"{key}={value}"
        out.append(line)
    target.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    (ROOT / ".secrets").mkdir(exist_ok=True)
    print("wrote .env and created .secrets/ (both git-ignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
