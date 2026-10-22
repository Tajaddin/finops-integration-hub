"""A reproducible book of business for a seat-based SaaS company selling in USD, EUR and GBP.

Same seed, same accounts, same deals, same amounts. Amounts come from the price book, except for a small, recorded
share of won deals with the data problems sales teams really produce (a mistyped amount, a missing plan, an
unapproved discount); those must end up in the dead-letter table, not on an invoice. Some customers already exist
in the ERP (created by finance before the integration, matched by website), so the sync must not duplicate them.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from hub.domain import Plan, Term
from hub.pricing import ALLOWED_DEAL_DISCOUNTS, deal_amount

PREFIXES = [
    "North",
    "Blue",
    "Cedar",
    "Harbor",
    "Summit",
    "Maple",
    "Iron",
    "Silver",
    "Bright",
    "Clear",
    "Pine",
    "Stone",
    "River",
    "Oak",
    "Lumen",
    "Atlas",
    "Vector",
    "Beacon",
    "Granite",
    "Juniper",
    "Meadow",
    "Orbit",
    "Pioneer",
    "Quarry",
]
SUFFIXES = [
    "field",
    "gate",
    "ridge",
    "point",
    "brook",
    "works",
    "line",
    "path",
    "crest",
    "haven",
    "dale",
    "mark",
]
KINDS = ["Labs", "Systems", "Academy", "Health", "Logistics", "Analytics", "Partners", "Schools", "Software", "Group"]
COUNTRIES = [("US", "USD", 0.70), ("DE", "EUR", 0.08), ("FR", "EUR", 0.05), ("IE", "EUR", 0.05), ("GB", "GBP", 0.12)]
STAGES = [
    ("Closed Won", 0.45),
    ("Closed Lost", 0.20),
    ("Negotiation", 0.12),
    ("Proposal", 0.13),
    ("Qualification", 0.10),
]
PLANS = [(Plan.STARTER, 0.30), (Plan.TEAM, 0.35), (Plan.BUSINESS, 0.25), (Plan.ENTERPRISE, 0.10)]
DISCOUNTS = [(0, 0.45), (5, 0.20), (10, 0.20), (15, 0.10), (20, 0.05)]
BAD_DATA_RATE = 0.03  # share of won deals with a data problem a person must fix


@dataclass
class SeedAccount:
    key: str
    name: str
    website: str
    country: str
    currency: str
    in_erp_already: bool


@dataclass
class SeedDeal:
    key: str
    account_key: str
    name: str
    stage: str
    plan: str | None
    seats: int
    term: str
    discount_pct: int
    currency: str
    amount: str
    close_date: str
    data_problem: str | None = None


@dataclass
class Book:
    seed: int
    accounts: list[SeedAccount] = field(default_factory=list)
    deals: list[SeedDeal] = field(default_factory=list)

    def manifest(self) -> dict[str, Any]:
        won = [d for d in self.deals if d.stage == "Closed Won"]
        return {
            "seed": self.seed,
            "accounts": len(self.accounts),
            "accounts_already_in_erp": sum(a.in_erp_already for a in self.accounts),
            "deals": len(self.deals),
            "won_deals": len(won),
            "won_deals_with_data_problems": sum(1 for d in won if d.data_problem),
            "won_by_currency": {c: sum(1 for d in won if d.currency == c) for c in ("USD", "EUR", "GBP")},
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest(),
            "accounts": [asdict(a) for a in self.accounts],
            "deals": [asdict(d) for d in self.deals],
        }


def _pick[T](rng: random.Random, weighted: list[tuple[T, float]]) -> T:
    return rng.choices([v for v, _ in weighted], weights=[w for _, w in weighted], k=1)[0]


def generate(accounts: int = 300, seed: int = 42, start: date = date(2025, 11, 1), days: int = 350) -> Book:
    rng = random.Random(seed)  # noqa: S311  (seeded on purpose: same seed, same book)
    book = Book(seed=seed)
    used: set[str] = set()
    for i in range(accounts):
        while True:
            stem = rng.choice(PREFIXES) + rng.choice(SUFFIXES)
            kind = rng.choice(KINDS)
            if (stem, kind) not in used:
                used.add((stem, kind))  # type: ignore[arg-type]
                break
        country, currency = _pick(rng, [((c, cur), w) for c, cur, w in COUNTRIES])
        tld = {"US": "com", "DE": "de", "FR": "fr", "IE": "ie", "GB": "co.uk"}[country]
        book.accounts.append(
            SeedAccount(
                key=f"A{i:04d}",
                name=f"{stem} {kind}",
                website=f"https://www.{stem.lower()}{kind.lower()}.{tld}",
                country=country,
                currency=currency,
                in_erp_already=rng.random() < 0.10,
            )
        )
    n = 0
    for account in book.accounts:
        for _ in range(rng.choice([1, 1, 2, 2, 3, 4])):
            stage = _pick(rng, STAGES)
            plan = _pick(rng, PLANS)
            seats = max(1, min(800, int(rng.lognormvariate(3.2, 0.9))))
            term = Term.ANNUAL if rng.random() < 0.6 else Term.MONTHLY
            discount = _pick(rng, DISCOUNTS)
            amount = deal_amount(plan, seats, term, account.currency, discount)
            close = start + timedelta(days=rng.randrange(days))
            deal = SeedDeal(
                key=f"D{n:05d}",
                account_key=account.key,
                name=f"{account.name} - {plan.value.title()} {seats} seats",
                stage=stage,
                plan=plan.value,
                seats=seats,
                term=term.value,
                discount_pct=discount,
                currency=account.currency,
                amount=str(amount),
                close_date=close.isoformat(),
            )
            if stage == "Closed Won" and rng.random() < BAD_DATA_RATE:
                problem = rng.choice(["amount_typo", "missing_plan", "unapproved_discount"])
                deal.data_problem = problem
                if problem == "amount_typo":
                    deal.amount = str((amount * 10).quantize(Decimal("0.01")))  # an extra zero
                elif problem == "missing_plan":
                    deal.plan = None
                else:
                    deal.discount_pct = rng.choice([d for d in (7, 12, 25, 30) if d not in ALLOWED_DEAL_DISCOUNTS])
            book.deals.append(deal)
            n += 1
    return book
