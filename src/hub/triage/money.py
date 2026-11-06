"""Money written in a customer message, read for the refund-approval rule (#23).

The rule needs the largest amount a message could mean, so where a number has two readings the larger one wins:
"$2.400" is 2,400 (European grouping), not 2.40. Separators may be commas, dots, spaces or apostrophes, the
currency a symbol or an ISO code before or after the number, and a k/m suffix multiplies. A currency written with
a number the reader cannot read ("a few thousand dollars", "$1.2.3") is reported as unreadable, and the rule holds
the refund rather than guess; so is any number with letters that look like digits glued to it ("24OO"). An amount
need not carry a currency at all ("refund 2400", "Amount (USD): 2,400"), so the rule also reads every other number
in the message (`numbers`), invoice numbers and dates aside.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INVOICE_NUMBER = re.compile(r"\bINV/\d{4}/\d{5}\b")
_SYMBOL = r"(?:(?:US|CA|AU|NZ|HK|SG|C|A|R|S)?\$|[€£¥₹₺₼])"
_CODE = r"(?:USD|EUR|GBP|CAD|AUD|NZD|CHF|JPY|CNY|INR|TRY|AZN|SEK|NOK|DKK|PLN|MXN|BRL|ZAR|SGD|HKD|AED)"
_WORD = r"(?:dollars?|dolar(?:es)?|bucks|euros?|pounds?|quid|yen|lira|manat|rupees?)"
# digits joined by separators; a space (of any width) only before a group of exactly three digits ("2 400", not "40 2026").
# A number never starts inside another one ("123 456" is not tried again from "456"): a pattern that fails after a
# long run of digit groups would otherwise retry from every group, quadratic in the message's length.
_NUMBER = r"(?<!\d)(?<!\d[.,'\u2019])(?!(?<=\d\s)\d{3}(?!\d))\d+(?:(?:[.,'\u2019]|\s(?=\d{3}(?!\d)))\d+)*"
_SUFFIX = r"(?:k|m|mn|bn|\s?thousand|\s?million|\s?billion|\s?grand)\b"
_DASH = r"(?:[.,][-\N{EN DASH}])?"  # "2.400,- EUR", "CHF 1'200.-"
MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "grand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
# The currency before the number and after it are separate patterns, so "5 USD 2,400" yields both amounts. Before
# a number a code counts only in capitals ("try 3 times" is not Turkish lira); after one in any case ("2400 eur"),
# except TRY ("give it 1 try" must not pass for an amount and make a refund look small). Letters glued to the number
# make it unreadable ("$2,4OO").
BEFORE = re.compile(rf"(?:{_SYMBOL}|(?-i:\b{_CODE}))\s*(?P<n>{_NUMBER})(?P<s>{_SUFFIX})?{_DASH}(?P<x>[^\W\d_]*)", re.I)
AFTER = re.compile(
    rf"(?P<n>{_NUMBER})(?P<s>{_SUFFIX})?{_DASH}\s*(?:{_SYMBOL}|(?:(?!try\b){_CODE}|(?-i:TRY))\b|{_WORD}\b)", re.I
)
# Without a currency only letters that look like digits make a number unreadable ("24OO", "2I00", "1O,400"), so
# units and ordinals ("10oz", "2nd", "10am") still read as the number they follow.
BARE = re.compile(rf"(?P<n>{_NUMBER})(?P<s>{_SUFFIX})?(?P<x>(?-i:[OoIl]+)(?:{_SUFFIX})?(?![^\W\d_]))?", re.I)
_CARDINAL = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty"
    r"|seventy|eighty|ninety|hundreds?|thousands?|millions?|billions?|dozens?)"
)
_FILLER = r"(?:a|an|few|several|couple|of|and|some|many)"
# A currency with words where the number should be: "two thousand dollars", "thousands of euros", "$ several
# hundred". A run of number words is matched whole and then checked for a cardinal and a currency after it, for the
# same reason as _NUMBER: one pattern doing both would retry from every word of a long run.
NUMBER_WORDS = re.compile(rf"\b(?:{_CARDINAL}|{_FILLER})\b(?:[\s-]+(?:{_CARDINAL}|{_FILLER})\b)*", re.I)
CARDINAL = re.compile(rf"\b{_CARDINAL}\b", re.I)
CURRENCY_AFTER_WORDS = re.compile(rf"[\s-]+(?:{_WORD}|grand|{_CODE})\b", re.I)
SYMBOL_WORDS = re.compile(rf"{_SYMBOL}\s*(?:{_FILLER}\s+)*{_CARDINAL}\b", re.I)
MAGNITUDE = re.compile(r"\b(?:hundred|thousand|million|billion)s?\b", re.I)
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?"
    r"|nov(?:ember)?|dec(?:ember)?)\.?"
)
_YEAR = r"(?:19|20)\d{2}"
# dates are not amounts: 2026-10-01, 01.10.2026, 10/01/2026, March 3, 2026, 3 March 2026, Oct 2026
DATE = re.compile(
    rf"\b(?:{_YEAR}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}([./-])\d{{1,2}}\1{_YEAR}"
    rf"|{_MONTH}\s+(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s+)?{_YEAR}|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH},?\s+{_YEAR})\b",
    re.I,
)


@dataclass(frozen=True)
class Mention:
    text: str
    readings: frozenset[float]  # every value the number can mean; empty when it cannot be read

    @property
    def value(self) -> float | None:
        """The largest reading, or None when the number cannot be read."""
        return max(self.readings) if self.readings else None


def _grouped(groups: list[str]) -> bool:
    """1-3 digits, then groups of exactly three: the shape of a thousands-grouped number."""
    return all(g.isdigit() for g in groups) and 1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])


def readings(token: str) -> frozenset[float]:  # noqa: PLR0911  (one return per shape of number)
    """Every value a written number can mean as money; empty when its separators make no sense."""
    s = re.sub(r"[\s'\u2019]", "", token)  # spaces (any width) and apostrophes only ever group thousands
    seps = {c for c in s if c in ".,"}
    if not seps:
        return frozenset({float(s)})
    last = max(s.rfind("."), s.rfind(","))
    head, tail = s[:last], s[last + 1 :]
    if len(seps) == 2:  # "1.200,00", "1,200.00": the last separator is the decimal point
        groups = head.split("," if s[last] == "." else ".")
        if s[last] in head or not _grouped(groups) or len(tail) > 2:
            return frozenset()
        return frozenset({float("".join(groups) + "." + tail)})
    groups = s.split(s[last])
    if len(groups) > 2:  # "1.200.000", "1,200,000"
        return frozenset({float("".join(groups))}) if _grouped(groups) else frozenset()
    if len(tail) == 3:  # "2,400" or "2.400": grouping (money has two decimals) or, read literally, a decimal
        return frozenset({float(head + tail), float(head + "." + tail)})
    if len(tail) <= 2:
        return frozenset({float(head + "." + tail)})
    return frozenset()


def _mention(m: re.Match[str]) -> Mention:
    if m.groupdict().get("x"):
        return Mention(m.group(0), frozenset())
    values = readings(m["n"])
    if m["s"]:
        factor = MULTIPLIERS[m["s"].strip().lower()]
        values = frozenset(round(v * factor, 2) for v in values)
    return Mention(m.group(0).strip(), values)


def _blank(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _masked(text: str) -> str:
    return _blank(INVOICE_NUMBER, text)  # invoice numbers are not amounts


def mentions(text: str) -> list[Mention]:
    """Every amount written with a currency, in the order written, plus each currency written with words for a
    number (unreadable)."""
    text = _masked(text)
    found = [(m.start(), _mention(m)) for m in BEFORE.finditer(text)]
    found += [(m.start(), _mention(m)) for m in AFTER.finditer(text)]
    for m in NUMBER_WORDS.finditer(text):
        currency = CURRENCY_AFTER_WORDS.match(text, m.end())
        if currency and CARDINAL.search(m.group(0)):
            found.append((m.start(), Mention(text[m.start() : currency.end()], frozenset())))
    found += [(m.start(), Mention(m.group(0), frozenset())) for m in SYMBOL_WORDS.finditer(text)]
    return [mention for _, mention in sorted(found, key=lambda x: x[0])]


def numbers(text: str) -> list[Mention]:
    """Every number written in the message, with or without a currency, except invoice numbers and dates; and each
    number written in words with a magnitude ("two thousand", "a few hundred"), which cannot be read."""
    text = _blank(DATE, _masked(text))
    found = [(m.start(), _mention(m)) for m in BARE.finditer(text)]
    found += [
        (m.start(), Mention(m.group(0), frozenset())) for m in NUMBER_WORDS.finditer(text) if MAGNITUDE.search(m[0])
    ]
    return [mention for _, mention in sorted(found, key=lambda x: x[0])]


def read(value: str) -> float | None:
    """The amount in a short string such as a model's answer ("1.200,00", "$2.4k"): its largest reading."""
    found = BARE.search(_masked(value))
    return _mention(found).value if found else None
