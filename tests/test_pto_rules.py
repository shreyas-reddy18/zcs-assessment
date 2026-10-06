"""Unit tests for the deterministic PTO rules, using the real rules from config/pto_policy_rules.json."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from mcp_server.pto_rules import (BlackoutPeriod, EditionRules, ExistingRequest, LeadTimeRule, business_days,
                                  evaluate_request)

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "pto_policy_rules.json").read_text())


def _rules(year: int) -> EditionRules:
    r = CONFIG["plan_years"][str(year)]
    return EditionRules(
        plan_year=year,
        doc_id=r["source_doc_id"],
        title=f"PTO & Leave Policy -- {year}",
        lead_time_rules=[LeadTimeRule(x["min_business_days"], x["max_business_days"], x["min_notice_hours"],
                                      x["section"], x.get("note")) for x in r["lead_time_rules"]],
        blackout_periods=[BlackoutPeriod(x["name"], date.fromisoformat(x["start_date"]),
                                         date.fromisoformat(x["end_date"]), x["max_days_allowed"], x["section"])
                          for x in r["blackout_periods"]],
        non_working_days={date.fromisoformat(x["date"]): x["name"] for x in r["non_working_days"]},
    )


RULES = {2025: _rules(2025), 2026: _rules(2026)}


def evaluate(start, end, *, today=date(2026, 5, 15), balance="12", pending="0", existing=(), rules=RULES):
    return evaluate_request(start=start, end=end, today=today, rules_by_year=rules,
                            balance_days=Decimal(balance), pending_days=Decimal(pending),
                            existing_requests=list(existing))


def failed(ev):
    return {c.rule for c in ev.checks if not c.passed}


def test_week_of_july_13_2026_is_rejected_by_summer_launch_blackout():
    ev = evaluate(date(2026, 7, 13), date(2026, 7, 17))
    assert not ev.eligible
    assert failed(ev) == {"blackout_period"}
    assert ev.governing_editions[0]["doc_id"] == "pto_2026"


def test_two_days_inside_blackout_pass_with_manager_discretion_warning():
    ev = evaluate(date(2026, 7, 9), date(2026, 7, 10))
    assert ev.eligible
    assert any("Summer Product Launch" in w for w in ev.warnings)


def test_same_july_week_in_2025_had_no_blackout():
    ev = evaluate(date(2025, 7, 14), date(2025, 7, 18), today=date(2025, 5, 1))
    assert ev.eligible


def test_lead_time_differs_by_edition():
    # 3 business days with 10 days' notice: fine under 2025 (1 week), too short under 2026 (2 weeks).
    assert evaluate(date(2025, 3, 11), date(2025, 3, 13), today=date(2025, 3, 1)).eligible
    ev = evaluate(date(2026, 8, 11), date(2026, 8, 13), today=date(2026, 8, 1))
    assert failed(ev) == {"lead_time"}


def test_pending_requests_reduce_available_balance():
    ev = evaluate(date(2026, 9, 14), date(2026, 9, 18), balance="7.5", pending="3")
    assert failed(ev) == {"sufficient_balance"}


def test_holidays_and_weekends_are_not_counted():
    # Thu Nov 26 - Wed Dec 2 2026: Thanksgiving + day after + weekend excluded -> 3 business days.
    assert len(business_days(date(2026, 11, 26), date(2026, 12, 2), RULES)) == 3


def test_winter_shutdown_only_range_needs_no_pto():
    ev = evaluate(date(2026, 12, 24), date(2026, 12, 31))
    assert "has_working_days" in failed(ev)


def test_year_without_published_edition_cannot_be_validated():
    ev = evaluate(date(2027, 7, 12), date(2027, 7, 16))
    assert not ev.eligible
    assert failed(ev) == {"governing_edition_available"}


def test_request_spanning_into_unpublished_year_cannot_be_validated():
    assert failed(evaluate(date(2026, 12, 30), date(2027, 1, 5))) == {"governing_edition_available"}


def test_overlap_with_existing_request_is_rejected():
    existing = [ExistingRequest("PTO-1", date(2026, 9, 15), date(2026, 9, 16), "pending")]
    assert failed(evaluate(date(2026, 9, 14), date(2026, 9, 18), existing=existing)) == {"no_overlapping_request"}


def test_denied_requests_do_not_block():
    existing = [ExistingRequest("PTO-1", date(2026, 9, 15), date(2026, 9, 16), "denied")]
    assert evaluate(date(2026, 9, 14), date(2026, 9, 18), existing=existing).eligible


def test_past_start_date_is_rejected():
    assert "start_date_in_future" in failed(evaluate(date(2026, 5, 1), date(2026, 5, 1)))


@pytest.mark.parametrize("days,expect_note", [(9, False), (10, True)])
def test_ten_day_requests_flag_out_of_office_plan(days, expect_note):
    # Sep 8 2026 (Tue, after Labor Day) onward, 30+ days out.
    end = {9: date(2026, 9, 18), 10: date(2026, 9, 21)}[days]
    ev = evaluate(date(2026, 9, 8), end, balance="20")
    assert ev.business_days == days
    assert ev.eligible
    assert any("out-of-office" in w for w in ev.warnings) == expect_note
