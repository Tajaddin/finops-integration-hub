"""The labelled messages: Bitext's customer-support set (26,872 utterances, 27 intents, CDLA-Sharing-1.0), with its
`{{Placeholder}}` slots filled deterministically so messages carry real-looking invoice numbers and refund
amounts, split by a fixed seed into train and test, stratified by intent.

The evaluation sample is a stratified slice of the test split: the same messages for every approach.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DATASET = "bitext/Bitext-customer-support-llm-chatbot-training-dataset"
LOCAL = Path("data/triage/bitext.parquet")
SLOT = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
FIRST = ["Maria", "James", "Aisha", "Chen", "Olivia", "Diego", "Fatima", "Noah", "Elena", "Kwame", "Sofia", "Liam"]
LAST = ["Garcia", "Smith", "Khan", "Wang", "Brown", "Lopez", "Ahmed", "Miller", "Rossi", "Mensah", "Novak", "Kim"]


@dataclass(frozen=True)
class Message:
    id: str
    text: str
    intent: str
    category: str
    refund_amount: float | None  # the amount written into the message, when it has one


def load_raw(path: Path = LOCAL) -> pd.DataFrame:
    if not path.is_file():
        from datasets import load_dataset

        path.parent.mkdir(parents=True, exist_ok=True)
        load_dataset(DATASET, split="train").to_parquet(str(path))
    return pd.read_parquet(path)


def fill(text: str, rng: random.Random, invoice_numbers: list[str]) -> tuple[str, float | None]:
    amount: float | None = None
    symbol = rng.choice(["$", "$", "$", "€", "£"])

    def value(m: re.Match[str]) -> str:  # noqa: PLR0911  (one branch per placeholder)
        nonlocal amount
        slot = m.group(1).lower()
        if slot == "refund amount":
            amount = float(rng.choice([19, 49, 120, 240, 380, 499, 650, 900, 1250, 2400, 4800]))
            return f"{amount:,.2f}"
        if slot == "currency symbol":
            return symbol
        if slot in ("order number", "invoice number"):
            return rng.choice(invoice_numbers) if invoice_numbers else f"INV/2026/{rng.randint(1, 400):05d}"
        if slot == "person name":
            return f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if slot in ("account type", "account category"):
            return rng.choice(["Business", "Team", "Enterprise", "Starter"])
        if slot == "delivery city":
            return rng.choice(["Tampa", "Dublin", "Lyon", "Leeds", "Austin"])
        if slot == "delivery country":
            return rng.choice(["United States", "Ireland", "France", "United Kingdom"])
        return m.group(0)

    filled = SLOT.sub(value, text)
    if amount is not None and symbol not in filled:
        filled = filled.replace(f"{amount:,.2f}", f"{symbol}{amount:,.2f}", 1)
    return filled, amount


def messages(seed: int = 42, invoice_numbers: list[str] | None = None, path: Path = LOCAL) -> list[Message]:
    df = load_raw(path)
    rng = random.Random(seed)  # noqa: S311  (reproducible text, not security)
    out = []
    for i, row in enumerate(df.itertuples(index=False)):
        text, amount = fill(str(row.instruction), rng, invoice_numbers or [])
        out.append(Message(f"m{i:05d}", text, str(row.intent), str(row.category), amount))
    return out


def split(msgs: list[Message], test_share: float = 0.2, seed: int = 42) -> tuple[list[Message], list[Message]]:
    by_intent: dict[str, list[Message]] = {}
    for m in msgs:
        by_intent.setdefault(m.intent, []).append(m)
    rng = random.Random(seed)  # noqa: S311
    train, test = [], []
    for intent in sorted(by_intent):
        group = by_intent[intent][:]
        rng.shuffle(group)
        cut = round(len(group) * test_share)
        test += group[:cut]
        train += group[cut:]
    return train, test


def eval_sample(test: list[Message], per_intent: int = 40, seed: int = 7) -> list[Message]:
    rng = random.Random(seed)  # noqa: S311
    out = []
    for intent in sorted({m.intent for m in test}):
        group = [m for m in test if m.intent == intent]
        out += rng.sample(group, min(per_intent, len(group)))
    return out
