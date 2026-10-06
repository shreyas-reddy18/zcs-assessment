"""All BigQuery access for the MCP tools.

Authorization rule: any query that returns an employee's HR data joins through
visible_employees(@caller), the table function in sql/02_access.sql. The caller
email always comes from the verified request identity, never from tool input.
The only query not scoped this way is find_in_directory(), which returns
directory fields (name, department, title) and no HR data.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from . import bq, config
from .pto_rules import BlackoutPeriod, EditionRules, ExistingRequest, LeadTimeRule

T = config.table


def visible() -> str:
    return f"{T('visible_employees')}(@caller)"


# --- identity -----------------------------------------------------------------

def resolve_caller(caller: str) -> dict | None:
    rows = bq.query(
        f"""
        SELECT im.google_email, im.access_tier, e.employee_id, e.full_name, e.job_title, e.department,
               EXISTS(SELECT 1 FROM {T('employees')} r WHERE r.manager_id = e.employee_id) AS has_reports
        FROM {T('identity_map')} im
        JOIN {T('employees')} e USING (employee_id)
        WHERE LOWER(im.google_email) = LOWER(@caller)
        """,
        caller=caller,
    )
    return rows[0] if rows else None


def direct_reports(caller: str) -> list[dict]:
    return bq.query(
        f"""
        SELECT e.employee_id, e.full_name, e.job_title, e.department
        FROM {visible()} v JOIN {T('employees')} e USING (employee_id)
        WHERE v.access_basis = 'direct_report'
        ORDER BY e.employee_id
        """,
        caller=caller,
    )


def find_in_directory(caller: str, name_or_id: str) -> list[dict]:
    """Directory lookup (name, department, title) plus whether the caller may see HR data."""
    return bq.query(
        f"""
        SELECT e.employee_id, e.full_name, e.department, e.job_title,
               v.employee_id IS NOT NULL AS caller_can_view_records,
               v.access_basis
        FROM {T('employees')} e
        LEFT JOIN {visible()} v USING (employee_id)
        WHERE UPPER(e.employee_id) = UPPER(@q)
           OR LOWER(e.full_name) LIKE CONCAT('%', LOWER(@q), '%')
        ORDER BY e.employee_id
        LIMIT 10
        """,
        caller=caller, q=name_or_id.strip(),
    )


# --- reads (authorization-scoped) ---------------------------------------------

def pto_positions(caller: str, employee_ids: list[str] | None = None) -> list[dict]:
    return bq.query(
        f"""
        SELECT v.access_basis, p.*
        FROM {visible()} v JOIN {T('employee_pto_position')} p USING (employee_id)
        WHERE COALESCE(ARRAY_LENGTH(@ids), 0) = 0 OR p.employee_id IN UNNEST(@ids)
        ORDER BY p.employee_id
        """,
        caller=caller, ids=employee_ids or [],
    )


def pto_requests(
    caller: str,
    *,
    employee_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    year: int | None = None,
    direct_reports_only: bool = False,
) -> list[dict]:
    return bq.query(
        f"""
        SELECT r.request_id, r.employee_id, e.full_name, e.department, r.start_date, r.end_date,
               r.days_requested, r.status, r.submitted_date, r.approver_id, r.reason, r.source
        FROM {visible()} v
        JOIN {T('pto_requests')} r USING (employee_id)
        JOIN {T('employees')} e USING (employee_id)
        WHERE (COALESCE(ARRAY_LENGTH(@ids), 0) = 0 OR r.employee_id IN UNNEST(@ids))
          AND (COALESCE(ARRAY_LENGTH(@statuses), 0) = 0 OR r.status IN UNNEST(@statuses))
          AND (@year IS NULL OR EXTRACT(YEAR FROM r.start_date) = @year)
          AND (NOT @reports_only OR v.access_basis = 'direct_report')
        ORDER BY r.employee_id, r.start_date
        """,
        caller=caller,
        ids=employee_ids or [],
        statuses=statuses or [],
        year=("INT64", year),
        reports_only=direct_reports_only,
    )


# --- policy rules --------------------------------------------------------------

def edition_rules(years: list[int]) -> dict[int, EditionRules]:
    ids = [str(y) for y in years]
    eds = bq.query(
        f"""
        SELECT plan_year, doc_id, title FROM {T('policy_editions')}
        WHERE policy_type = 'PTO_and_Leave' AND CAST(plan_year AS STRING) IN UNNEST(@years)
        """,
        years=ids,
    )
    if not eds:
        return {}
    leads = bq.query(
        f"SELECT * FROM {T('pto_lead_time_rules')} WHERE CAST(plan_year AS STRING) IN UNNEST(@years)", years=ids)
    blackouts = bq.query(
        f"SELECT * FROM {T('pto_blackout_periods')} WHERE CAST(plan_year AS STRING) IN UNNEST(@years)", years=ids)
    off = bq.query(
        f"SELECT * FROM {T('non_working_days')} WHERE CAST(plan_year AS STRING) IN UNNEST(@years)", years=ids)

    out: dict[int, EditionRules] = {}
    for ed in eds:
        y = ed["plan_year"]
        out[y] = EditionRules(
            plan_year=y,
            doc_id=ed["doc_id"],
            title=ed["title"],
            lead_time_rules=[
                LeadTimeRule(r["min_business_days"], r["max_business_days"], r["min_notice_hours"],
                             r["source_section"], r["note"])
                for r in leads if r["plan_year"] == y
            ],
            blackout_periods=[
                BlackoutPeriod(r["name"], r["start_date"], r["end_date"], r["max_days_allowed"], r["source_section"])
                for r in blackouts if r["plan_year"] == y
            ],
            non_working_days={r["day"]: r["name"] for r in off if r["plan_year"] == y},
        )
    return out


def available_plan_years() -> list[int]:
    rows = bq.query(
        f"SELECT DISTINCT plan_year FROM {T('policy_editions')} "
        "WHERE policy_type = 'PTO_and_Leave' ORDER BY plan_year")
    return [r["plan_year"] for r in rows]


def existing_requests(caller: str, employee_id: str) -> list[ExistingRequest]:
    rows = pto_requests(caller, employee_ids=[employee_id], statuses=["pending", "approved"])
    return [ExistingRequest(r["request_id"], r["start_date"], r["end_date"], r["status"]) for r in rows]


# --- write path ------------------------------------------------------------------

def record_event(
    *,
    event_type: str,
    actor_email: str,
    employee_id: str | None,
    session_id: str | None,
    invocation_id: str | None,
    idempotency_key: str | None,
    start: date | None,
    end: date | None,
    eligible: bool | None = None,
    request_id: str | None = None,
    details: dict | None = None,
) -> None:
    bq.query(
        f"""
        INSERT INTO {T('pto_request_events')}
          (event_id, event_type, occurred_at, actor_email, employee_id, session_id, invocation_id,
           idempotency_key, request_id, start_date, end_date, eligible, details)
        VALUES (@event_id, @event_type, CURRENT_TIMESTAMP(), @actor, @employee_id, @session_id, @invocation_id,
                @key, @request_id, @start, @end, @eligible, @details)
        """,
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        actor=actor_email,
        employee_id=("STRING", employee_id),
        session_id=("STRING", session_id),
        invocation_id=("STRING", invocation_id),
        key=("STRING", idempotency_key),
        request_id=("STRING", request_id),
        start=("DATE", start),
        end=("DATE", end),
        eligible=("BOOL", eligible),
        details=("JSON", details),
    )


def latest_check(session_id: str, idempotency_key: str) -> dict | None:
    since = datetime.now(timezone.utc) - timedelta(minutes=config.CONFIRMATION_WINDOW_MINUTES)
    rows = bq.query(
        f"""
        SELECT invocation_id, eligible, occurred_at
        FROM {T('pto_request_events')}
        WHERE event_type = 'CHECKED' AND session_id = @session_id AND idempotency_key = @key
          AND occurred_at >= @since
        ORDER BY occurred_at DESC
        LIMIT 1
        """,
        session_id=session_id, key=idempotency_key, since=since,
    )
    return rows[0] if rows else None


def active_request_by_key(idempotency_key: str) -> dict | None:
    rows = bq.query(
        f"""
        SELECT request_id, employee_id, start_date, end_date, days_requested, status, submitted_date,
               approver_id, governing_doc_id, source_session_id, created_at
        FROM {T('pto_requests')}
        WHERE idempotency_key = @key AND status IN ('pending', 'approved')
        LIMIT 1
        """,
        key=idempotency_key,
    )
    return rows[0] if rows else None


def insert_request_once(
    *,
    idempotency_key: str,
    employee_id: str,
    approver_id: str | None,
    start: date,
    end: date,
    days: int,
    reason: str,
    governing_doc_id: str,
    submitted_by: str,
    session_id: str,
    invocation_id: str,
    submitted_date: date,
) -> tuple[str, bool]:
    """Insert the request unless an active one with this key exists. Returns (request_id, inserted)."""
    request_id = f"PTO-A{uuid.uuid4().hex[:8].upper()}"
    result = bq.query(
        f"""
        MERGE {T('pto_requests')} t
        USING (SELECT @key AS idempotency_key) s
        ON t.idempotency_key = s.idempotency_key AND t.status IN ('pending', 'approved')
        WHEN NOT MATCHED THEN INSERT
          (request_id, employee_id, start_date, end_date, days_requested, status, submitted_date, approver_id,
           reason, source, governing_doc_id, idempotency_key, submitted_by_email, source_session_id,
           source_invocation_id, created_at)
        VALUES
          (@request_id, @employee_id, @start, @end, @days, 'pending', @submitted_date, @approver_id,
           @reason, 'hr_assistant', @doc_id, @key, @submitted_by, @session_id, @invocation_id, CURRENT_TIMESTAMP())
        """,
        key=idempotency_key,
        request_id=request_id,
        employee_id=employee_id,
        start=start,
        end=end,
        days=Decimal(days),
        submitted_date=submitted_date,
        approver_id=("STRING", approver_id),
        reason=reason,
        doc_id=governing_doc_id,
        submitted_by=submitted_by,
        session_id=session_id,
        invocation_id=invocation_id,
    )
    return request_id, result[0]["num_dml_affected_rows"] == 1
