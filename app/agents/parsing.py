"""Deterministic parsers used by the dialogue manager's slot filling.

No external NLP dependencies: dates, times, money, timelines, phones, emails
and names are parsed with small, well-tested rules. The LLM-based NLU step
adds entities on top, but flows never *depend* on the LLM being available.
"""

from __future__ import annotations

import re
from datetime import date, time, timedelta
from decimal import Decimal, InvalidOperation

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

EMAIL_RE = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+")
PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
YES_RE = re.compile(
    r"^\s*(y|yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|correct|right|go ahead|please do"
    r"|book it|haan|ha)\b",
    re.I,
)
NO_RE = re.compile(r"^\s*(n|no|nope|nah|not now|cancel|don't|do not|wrong|change)\b", re.I)


# ------------------------------------------------------------------ dates & times
def parse_date(text: str, today: date) -> date | None:
    t = text.lower().strip()
    if re.search(r"\bday after tomorrow\b", t):
        return today + timedelta(days=2)
    if re.search(r"\b(today|tonight)\b", t):
        return today
    if re.search(r"\b(tomorrow|tmrw|tmr)\b", t):
        return today + timedelta(days=1)

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t)  # Indian format: dd/mm[/yyyy]
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else None
        if year is not None and year < 100:
            year += 2000
        return _resolve(day, month, year, today)

    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b(?:\s+(\d{{4}}))?", t)
    if m:
        return _resolve(int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)) if m.group(3) else None, today)
    m = re.search(rf"\b({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", t)
    if m:
        return _resolve(int(m.group(2)), MONTHS[m.group(1)], int(m.group(3)) if m.group(3) else None, today)

    m = re.search(
        r"\b(next|this|coming)?\s*("
        + "|".join(WEEKDAYS)
        + r"|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b",
        t,
    )
    if m:
        key = m.group(2)[:3]
        target = next(i for i, d in enumerate(WEEKDAYS) if d.startswith(key))
        # Naming today's weekday means the next one ("today" is handled above);
        # "next friday" is treated as the upcoming Friday, the common reading.
        delta = (target - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _resolve(day: int, month: int, year: int | None, today: date) -> date | None:
    if year is not None:
        return _safe_date(year, month, day)
    candidate = _safe_date(today.year, month, day)
    if candidate is not None and candidate < today:
        candidate = _safe_date(today.year + 1, month, day)
    return candidate


def parse_time(text: str) -> time | None:
    t = text.lower()
    m = re.search(r"\b(\d{1,2})(?::|\.)(\d{2})\s*(am|pm)?\b", t)
    if m:
        hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", t)
        if not m:
            if re.search(r"\bnoon\b", t):
                return time(12, 0)
            return None
        hour, minute, ampm = int(m.group(1)), 0, m.group(2)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return time(hour, minute)


# ------------------------------------------------------------------ contact details
def parse_email(text: str) -> str | None:
    m = EMAIL_RE.search(text)
    return m.group(0).lower() if m else None


def normalise_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if raw.strip().startswith("+"):
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return digits
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return None


def parse_phone(text: str) -> str | None:
    for m in PHONE_CANDIDATE_RE.finditer(text):
        phone = normalise_phone(m.group(0))
        if phone:
            return phone
    return None


_NAME_PREFIX = re.compile(
    r"^\s*(hi|hello|hey)?[,!\s]*(my name is|my name's|name is|i am|i'm|im|this is|it's|its|call me)\s+", re.I
)


def parse_name(text: str) -> str | None:
    candidate = _NAME_PREFIX.sub("", text.strip()).strip(" .!,")
    candidate = re.split(r"[,.;]| and | here\b", candidate, maxsplit=1)[0].strip()
    if not candidate or re.search(r"[\d@#/\\]", candidate):
        return None
    words = candidate.split()
    if not 1 <= len(words) <= 4 or any(len(w) > 30 for w in words):
        return None
    return " ".join(w[:1].upper() + w[1:] for w in words)


# ------------------------------------------------------------------ money & timelines
_MULTIPLIERS = {
    "k": 1_000,
    "thousand": 1_000,
    "l": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "cr": 10_000_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "m": 1_000_000,
    "mn": 1_000_000,
    "million": 1_000_000,
}


def parse_budget(text: str) -> Decimal | None:
    """'80 lakh' -> 8000000, '1.2 cr' -> 12000000, '₹50,000' -> 50000, '50k' -> 50000.

    For ranges ('50-60 lakh') the upper bound is returned.
    """
    t = text.lower().replace(",", "")
    best: Decimal | None = None
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?|l|crores?|cr|million|mn|m)?\b", t):
        try:
            value = Decimal(m.group(1))
        except InvalidOperation:
            continue
        unit = m.group(2)
        if unit is None:
            # A bare number followed later by a unit in a range: "50-60 lakh"
            tail = re.match(
                r"\s*(?:-|to)\s*\d+(?:\.\d+)?\s*(k|thousand|lakhs?|lacs?|l|crores?|cr|million|mn|m)\b",
                t[m.end() :],
            )
            unit = tail.group(1) if tail else None
        if unit:
            value *= _MULTIPLIERS[unit]
        if best is None or value > best:
            best = value
    return best


def parse_timeline_days(text: str) -> int | None:
    t = text.lower()
    rules: list[tuple[str, int]] = [
        (r"\b(asap|immediately|urgent|right away|now|this week)\b", 7),
        (r"\b(next week|in a week|within a week)\b", 14),
        (r"\b(this month|within a month|in a month|next 30 days)\b", 30),
        (r"\b(next month)\b", 45),
        (r"\b(this year|next year|just (browsing|looking)|no hurry|not sure)\b", 365),
    ]
    for pattern, days in rules:
        if re.search(pattern, t):
            return days
    m = re.search(r"(\d+)\s*(day|week|month|year)s?", t)
    if m:
        n = int(m.group(1))
        return n * {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(2)]
    return None


def is_yes(text: str) -> bool:
    return bool(YES_RE.search(text))


def is_no(text: str) -> bool:
    return bool(NO_RE.search(text))
