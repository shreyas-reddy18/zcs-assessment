"""Deterministic PTO request validation.

Pure functions only: no I/O, so every rule can be unit tested. The caller
loads the governing edition's rules from BigQuery and passes them in.

The governing edition is the plan year in which the leave is *taken*
(PTO & Leave Policy 2025, page 1: "It governs leave taken during the 2025
calendar year only"), not the year the request is submitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class LeadTimeRule:
    min_business_days: int
    max_business_days: int | None
    min_notice_hours: int
    section: str
    note: str | None = None


@dataclass(frozen=True)
class BlackoutPeriod:
    name: str
    start_date: date
    end_date: date
    max_days_allowed: int
    section: str


@dataclass(frozen=True)
class EditionRules:
    plan_year: int
    doc_id: str
    title: str
    lead_time_rules: list[LeadTimeRule]
    blackout_periods: list[BlackoutPeriod]
    non_working_days: dict[date, str]  # date -> name (holiday / shutdown)


@dataclass(frozen=True)
class ExistingRequest:
    request_id: str
    start_date: date
    end_date: date
    status: str


@dataclass
class Check:
    rule: str
    passed: bool
    detail: str
    source: str | None = None

    def as_dict(self) -> dict:
        return {"rule": self.rule, "passed": self.passed, "detail": self.detail, "source": self.source}


@dataclass
class Evaluation:
    eligible: bool
    governing_editions: list[dict]
    business_days: int
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "governing_editions": self.governing_editions,
            "business_days_requested": self.business_days,
            "checks": [c.as_dict() for c in self.checks],
            "warnings": self.warnings,
        }


def _cite(rules: EditionRules, section: str) -> str:
    return f"{rules.title}, section {section}"


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def business_days(start: date, end: date, rules_by_year: dict[int, EditionRules]) -> list[date]:
    """Weekdays in [start, end] that are not company holidays or shutdown days."""
    off: dict[date, str] = {}
    for r in rules_by_year.values():
        off.update(r.non_working_days)
    return [d for d in _days(start, end) if d.weekday() < 5 and d not in off]


def evaluate_request(
    *,
    start: date,
    end: date,
    today: date,
    rules_by_year: dict[int, EditionRules],
    balance_days: Decimal,
    pending_days: Decimal,
    existing_requests: list[ExistingRequest],
) -> Evaluation:
    """Evaluate a PTO request against every rule the governing edition imposes.

    rules_by_year holds whatever editions exist. A request touching a plan year
    with no published edition cannot be validated and is reported as such.
    """
    if end < start:
        return Evaluation(False, [], 0, [Check("valid_date_range", False, "End date is before start date.")])

    years = sorted({d.year for d in _days(start, end)})
    missing = [y for y in years if y not in rules_by_year]
    if missing:
        available = ", ".join(str(y) for y in sorted(rules_by_year)) or "none"
        return Evaluation(
            False, [], 0,
            [Check(
                "governing_edition_available", False,
                f"No PTO & Leave Policy edition has been published for {', '.join(map(str, missing))}, "
                f"so this request cannot be validated. Editions on file: {available}. "
                "Contact People Operations.",
            )],
        )

    editions = [rules_by_year[y] for y in years]
    governing = [{"plan_year": r.plan_year, "doc_id": r.doc_id, "title": r.title} for r in editions]
    # Lead time is a property of the request as a whole: the edition in force on its first day governs it.
    start_rules = rules_by_year[start.year]
    bdays = business_days(start, end, {y: rules_by_year[y] for y in years})
    n = len(bdays)
    checks: list[Check] = []
    warnings: list[str] = []

    checks.append(Check(
        "start_date_in_future", start > today,
        f"Starts {start.isoformat()}; today is {today.isoformat()}." if start > today
        else f"Start date {start.isoformat()} is not after today ({today.isoformat()}).",
    ))

    checks.append(Check(
        "has_working_days", n > 0,
        f"{n} business day(s) after excluding weekends, company holidays and shutdown days." if n
        else "The range contains no working days (weekends, holidays or shutdown only); no PTO is needed.",
    ))

    if n:
        tier = next(
            (t for t in start_rules.lead_time_rules
             if t.min_business_days <= n and (t.max_business_days is None or n <= t.max_business_days)),
            None,
        )
        if tier is not None:
            notice_hours = (start - today).days * 24
            ok = notice_hours >= tier.min_notice_hours
            checks.append(Check(
                "lead_time", ok,
                f"A {n}-business-day request needs at least {_hours_label(tier.min_notice_hours)} notice; "
                f"this request gives {(start - today).days} day(s).",
                _cite(start_rules, tier.section),
            ))
            if tier.note:
                warnings.append(tier.note)

    for r in editions:
        for b in r.blackout_periods:
            overlap = [d for d in bdays if b.start_date <= d <= b.end_date]
            if not overlap:
                continue
            if n > b.max_days_allowed:
                checks.append(Check(
                    "blackout_period", False,
                    f"Overlaps the {b.name} blackout ({b.start_date:%b %d} - {b.end_date:%b %d, %Y}); requests of "
                    f"more than {b.max_days_allowed} consecutive days are denied during a blackout.",
                    _cite(r, b.section),
                ))
            else:
                warnings.append(
                    f"Falls in the {b.name} blackout ({b.start_date:%b %d} - {b.end_date:%b %d, %Y}). Requests of "
                    f"{b.max_days_allowed} days or fewer are allowed at the manager's discretion "
                    f"({_cite(r, b.section)})."
                )
    if not any(c.rule == "blackout_period" for c in checks):
        checks.append(Check("blackout_period", True, "Does not conflict with a blackout period."))

    available = balance_days - pending_days
    checks.append(Check(
        "sufficient_balance", Decimal(n) <= available,
        f"Needs {n} day(s); balance {balance_days} minus {pending_days} already pending = {available} available.",
    ))

    clashes = [
        x for x in existing_requests
        if x.status in ("pending", "approved") and x.start_date <= end and x.end_date >= start
    ]
    checks.append(Check(
        "no_overlapping_request", not clashes,
        "No overlapping pending or approved request." if not clashes
        else "Overlaps existing request(s): " + ", ".join(
            f"{x.request_id} ({x.start_date} to {x.end_date}, {x.status})" for x in clashes),
    ))

    return Evaluation(all(c.passed for c in checks), governing, n, checks, warnings)


def _hours_label(hours: int) -> str:
    if hours % (24 * 7) == 0 and hours >= 24 * 7:
        weeks = hours // (24 * 7)
        return f"{weeks} week{'s' if weeks > 1 else ''}"
    if hours >= 72 and hours % 24 == 0:
        return f"{hours // 24} days"
    return f"{hours} hours"
