"""Explainable rule-based lead scoring (0-100) -> hot / warm / cold.

Deterministic on purpose: sales teams can see *why* a lead is hot, and the
score never changes because an LLM had a different day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.models.enums import LeadTemperature

HOT_THRESHOLD = 70
WARM_THRESHOLD = 40


@dataclass
class LeadScore:
    score: int
    temperature: LeadTemperature
    reasons: list[str] = field(default_factory=list)


def score_lead(
    *,
    phone: str | None,
    email: str | None,
    need: str | None,
    budget: str | None,
    budget_amount: Decimal | None,
    timeline_days: int | None,
    name: str | None = None,
) -> LeadScore:
    points = 0
    reasons: list[str] = []

    if phone:
        points += 20
        reasons.append("phone provided (+20)")
    if email:
        points += 10
        reasons.append("email provided (+10)")
    if name:
        points += 5
        reasons.append("name provided (+5)")
    if need and len(need.strip()) >= 3:
        points += 15
        reasons.append("clear need (+15)")
    if budget_amount is not None and budget_amount > 0:
        points += 20
        reasons.append("specific budget (+20)")
    elif budget:
        points += 8
        reasons.append("budget mentioned (+8)")

    if timeline_days is not None:
        if timeline_days <= 30:
            points += 30
            reasons.append("buying within a month (+30)")
        elif timeline_days <= 90:
            points += 18
            reasons.append("buying within 3 months (+18)")
        elif timeline_days <= 180:
            points += 8
            reasons.append("buying within 6 months (+8)")
        else:
            reasons.append("long or unsure timeline (+0)")

    score = min(points, 100)
    if score >= HOT_THRESHOLD:
        temperature = LeadTemperature.HOT
    elif score >= WARM_THRESHOLD:
        temperature = LeadTemperature.WARM
    else:
        temperature = LeadTemperature.COLD
    return LeadScore(score=score, temperature=temperature, reasons=reasons)
