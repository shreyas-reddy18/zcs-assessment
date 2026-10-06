"""Meridian HR MCP server: BigQuery records and the PTO write path.

Served over streamable HTTP on Cloud Run. Identity:
  * Cloud Run IAM admits only the agent's service account (no unauthenticated
    access), so these headers can only be set by the agent's code.
  * The agent sets X-Meridian-User from the ADK session's user_id, which the
    gateway set from a verified Google ID token. The model never supplies it,
    and no tool takes the caller's identity as an argument.
  * X-Meridian-Session / X-Meridian-Invocation tie writes and audit events to
    the conversation turn that produced them.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config, repository as repo
from .pto_rules import Evaluation, evaluate_request

log = logging.getLogger("meridian.mcp")

mcp = FastMCP(
    "meridian-hr",
    instructions="HR records and PTO requests for Meridian Dynamics, scoped to the signed-in employee.",
    stateless_http=True,
    json_response=True,
    host=os.getenv("HOST", "127.0.0.1"),
    port=int(os.getenv("PORT", "8080")),
)

ACCESS_POLICY = (
    "Employees can see their own record, managers can see their direct reports, "
    "and People Operations can see everyone."
)


@dataclass(frozen=True)
class Caller:
    email: str
    session_id: str | None
    invocation_id: str | None


class Unauthenticated(Exception):
    pass


def _caller(ctx: Context) -> Caller:
    request = ctx.request_context.request
    headers = request.headers if request is not None else {}
    email = (headers.get("x-meridian-user") or "").strip().lower()
    if not email:
        raise Unauthenticated()
    return Caller(email, headers.get("x-meridian-session"), headers.get("x-meridian-invocation"))


async def _run(fn, *args, **kwargs):
    # The BigQuery client is blocking; keep the event loop free.
    return await asyncio.to_thread(fn, *args, **kwargs)


def _unmapped(email: str) -> dict:
    return {
        "status": "unmapped",
        "message": f"The signed-in Google account {email} is not linked to a Meridian Dynamics employee record, "
                   "so no HR data can be shown. People Operations can add it to the identity map.",
    }


def _no_identity() -> dict:
    return {"status": "unauthenticated", "message": "No signed-in user was attached to this request."}


async def _resolve(ctx: Context) -> tuple[Caller, dict] | dict:
    try:
        caller = _caller(ctx)
    except Unauthenticated:
        return _no_identity()
    me = await _run(repo.resolve_caller, caller.email)
    if me is None:
        return _unmapped(caller.email)
    return caller, me


async def _target(caller: Caller, me: dict, employee_id: str) -> tuple[str, dict | None]:
    """Resolve an employee_id argument (blank = self) and explain a denial if there is one."""
    target = (employee_id or "").strip().upper() or me["employee_id"]
    if target == me["employee_id"]:
        return target, None
    matches = await _run(repo.find_in_directory, caller.email, target)
    match = next((m for m in matches if m["employee_id"] == target), None)
    if match is None:
        return target, {"status": "not_found", "message": f"No employee with ID {target}."}
    if not match["caller_can_view_records"]:
        return target, {
            "status": "forbidden",
            "message": f"You are not authorized to view the records of {match['full_name']} "
                       f"({match['department']}). {ACCESS_POLICY}",
        }
    return target, None


def _tier(me: dict) -> str:
    if me["access_tier"] == "people_ops":
        return "people_ops"
    return "manager" if me["has_reports"] else "employee"


# --- tools --------------------------------------------------------------------

@mcp.tool()
async def whoami(ctx: Context) -> dict:
    """Identify the signed-in employee: ID, name, title, access tier, direct reports, and today's date.

    Call this first whenever you need to know who is asking. The identity comes
    from Google sign-in; never ask the user who they are.
    """
    resolved = await _resolve(ctx)
    if isinstance(resolved, dict):
        return resolved
    caller, me = resolved
    reports = await _run(repo.direct_reports, caller.email)
    return {
        "status": "ok",
        "email": caller.email,
        "employee_id": me["employee_id"],
        "full_name": me["full_name"],
        "job_title": me["job_title"],
        "department": me["department"],
        "access_tier": _tier(me),
        "access_policy": ACCESS_POLICY,
        "direct_reports": reports,
        "today": config.today().isoformat(),
    }


@mcp.tool()
async def find_employee(ctx: Context, name_or_id: str) -> dict:
    """Look up employees by (partial) name or employee ID in the company directory.

    Returns directory fields only (ID, name, department, title) and whether the
    signed-in user may view each person's HR records. Use it to turn a name
    into an employee_id before calling the record tools.
    """
    resolved = await _resolve(ctx)
    if isinstance(resolved, dict):
        return resolved
    caller, _ = resolved
    matches = await _run(repo.find_in_directory, caller.email, name_or_id)
    return {"status": "ok", "matches": matches, "access_policy": ACCESS_POLICY}


@mcp.tool()
async def get_pto_summary(ctx: Context, employee_id: str = "") -> dict:
    """PTO position for one employee: balance, days used this year, pending days, and their open requests.

    Args:
        employee_id: Whose summary to fetch. Leave blank for the signed-in user.
            Others are only returned if the signed-in user is entitled to see them.
    """
    resolved = await _resolve(ctx)
    if isinstance(resolved, dict):
        return resolved
    caller, me = resolved
    target, denial = await _target(caller, me, employee_id)
    if denial:
        return denial
    positions = await _run(repo.pto_positions, caller.email, [target])
    if not positions:
        return {"status": "forbidden", "message": f"You are not authorized to view {target}. {ACCESS_POLICY}"}
    open_requests = await _run(
        repo.pto_requests, caller.email, employee_ids=[target], statuses=["pending", "approved"],
        year=config.today().year,
    )
    p = positions[0]
    return {
        "status": "ok",
        "employee_id": p["employee_id"],
        "full_name": p["full_name"],
        "department": p["department"],
        "pto_balance_days": p["pto_balance_days"],
        "pto_used_ytd": p["pto_used_ytd"],
        "pending_days": p["pending_days"],
        "available_after_pending": p["available_after_pending"],
        "requests_this_year": open_requests,
        "notes": "pto_balance_days and pto_used_ytd come from the HRIS. Pending requests have not been deducted "
                 "from the balance yet; available_after_pending nets them off.",
    }


@mcp.tool()
async def list_pto_requests(ctx: Context, employee_id: str = "", status: str = "", year: int = 0) -> dict:
    """List one employee's PTO requests, optionally filtered by status and year.

    Args:
        employee_id: Whose requests to list. Leave blank for the signed-in user.
        status: One of pending, approved, denied. Blank for all.
        year: Calendar year of the leave start date. 0 for all years.
    """
    resolved = await _resolve(ctx)
    if isinstance(resolved, dict):
        return resolved
    caller, me = resolved
    target, denial = await _target(caller, me, employee_id)
    if denial:
        return denial
    rows = await _run(
        repo.pto_requests, caller.email, employee_ids=[target],
        statuses=[status.lower()] if status else None, year=year or None,
    )
    return {"status": "ok", "employee_id": target, "requests": rows}


@mcp.tool()
async def list_team_pto_requests(ctx: Context, status: str = "pending", department: str = "") -> dict:
    """PTO requests across the people the signed-in user is entitled to see, excluding themselves.

    For a manager this is their direct reports. For People Operations it is the
    whole company, optionally narrowed to one department. Employees with no
    reports get an empty list.

    Args:
        status: pending, approved, denied, or blank for all.
        department: Optional department filter, e.g. "Engineering".
    """
    resolved = await _resolve(ctx)
    if isinstance(resolved, dict):
        return resolved
    caller, me = resolved
    rows = await _run(
        repo.pto_requests, caller.email,
        statuses=[status.lower()] if status else None,
        direct_reports_only=_tier(me) != "people_ops",
    )
    rows = [r for r in rows if r["employee_id"] != me["employee_id"]]
    if department:
        rows = [r for r in rows if (r["department"] or "").lower() == department.lower()]
    scope = "the whole company" if _tier(me) == "people_ops" else "your direct reports"
    return {"status": "ok", "scope": scope, "requests": rows}


def _idempotency_key(employee_id: str, start: date, end: date) -> str:
    return hashlib.sha256(f"{employee_id}|{start.isoformat()}|{end.isoformat()}".encode()).hexdigest()


def _parse_dates(start_date: str, end_date: str) -> tuple[date, date] | dict:
    try:
        return date.fromisoformat(start_date.strip()), date.fromisoformat(end_date.strip())
    except ValueError:
        return {"status": "invalid_input", "message": "Dates must be YYYY-MM-DD."}


async def _evaluate(caller: Caller, me: dict, start: date, end: date) -> tuple[Evaluation, dict]:
    years = list(range(start.year, end.year + 1))
    rules, positions, existing = await asyncio.gather(
        _run(repo.edition_rules, years),
        _run(repo.pto_positions, caller.email, [me["employee_id"]]),
        _run(repo.existing_requests, caller.email, me["employee_id"]),
    )
    position = positions[0]
    evaluation = evaluate_request(
        start=start,
        end=end,
        today=config.today(),
        rules_by_year=rules,
        balance_days=Decimal(position["pto_balance_days"]),
        pending_days=Decimal(position["pending_days"]),
        existing_requests=existing,
    )
    return evaluation, position


@mcp.tool()
async def check_pto_request(ctx: Context, start_date: str, end_date: str) -> dict:
    """Step 1 of 2: validate a PTO request for the signed-in employee against the governing policy. Writes nothing.

    Checks balance (net of pending requests), lead time, blackout periods,
    overlap with existing requests, and that a policy edition exists for the
    dates. The governing edition is the plan year in which the leave is taken.
    Show the employee every check and ask them to confirm before calling
    submit_pto_request. Also use this to answer "can I take X off?".

    Args:
        start_date: First day of leave, YYYY-MM-DD.
        end_date: Last day of leave (inclusive), YYYY-MM-DD.
    """
    resolved = await _resolve(ctx)
    if isinstance(resolved, dict):
        return resolved
    caller, me = resolved
    parsed = _parse_dates(start_date, end_date)
    if isinstance(parsed, dict):
        return parsed
    start, end = parsed
    evaluation, _ = await _evaluate(caller, me, start, end)
    key = _idempotency_key(me["employee_id"], start, end)
    await _run(
        repo.record_event, event_type="CHECKED", actor_email=caller.email, employee_id=me["employee_id"],
        session_id=caller.session_id, invocation_id=caller.invocation_id, idempotency_key=key,
        start=start, end=end, eligible=evaluation.eligible, details=evaluation.as_dict(),
    )
    result = {"status": "ok", "employee_id": me["employee_id"], "start_date": start.isoformat(),
              "end_date": end.isoformat(), **evaluation.as_dict()}
    if evaluation.eligible:
        result["next_step"] = (
            "Show these checks to the employee and ask them to confirm. Call submit_pto_request with the same "
            "dates only after they confirm in their next message."
        )
    else:
        result["next_step"] = "Explain which checks failed and why, citing the policy section. Do not submit."
    return result


_submit_locks: dict[str, asyncio.Lock] = {}


@mcp.tool()
async def submit_pto_request(ctx: Context, start_date: str, end_date: str, reason: str = "") -> dict:
    """Step 2 of 2: write a PTO request for the signed-in employee, exactly once.

    Only works after check_pto_request passed for the same dates in this
    conversation and the employee confirmed in a later message. Re-validates
    before writing. Calling it again for the same dates returns the existing
    request and does not create a duplicate.

    Args:
        start_date: First day of leave, YYYY-MM-DD (same as the check).
        end_date: Last day of leave, YYYY-MM-DD (same as the check).
        reason: Optional short reason, e.g. "Family vacation".
    """
    resolved = await _resolve(ctx)
    if isinstance(resolved, dict):
        return resolved
    caller, me = resolved
    parsed = _parse_dates(start_date, end_date)
    if isinstance(parsed, dict):
        return parsed
    start, end = parsed
    emp = me["employee_id"]
    key = _idempotency_key(emp, start, end)
    event = dict(actor_email=caller.email, employee_id=emp, session_id=caller.session_id,
                 invocation_id=caller.invocation_id, idempotency_key=key, start=start, end=end)

    lock = _submit_locks.setdefault(key, asyncio.Lock())
    async with lock:
        existing = await _run(repo.active_request_by_key, key)
        if existing:
            await _run(repo.record_event, event_type="DUPLICATE", request_id=existing["request_id"], **event)
            return {"status": "already_submitted", "request": existing,
                    "message": "A request for these exact dates already exists. No new record was created."}

        check = await _run(repo.latest_check, caller.session_id, key) if caller.session_id else None
        if check is None or not check["eligible"]:
            await _run(repo.record_event, event_type="CONFIRMATION_MISSING",
                       details={"reason": "no passing check"}, **event)
            return {"status": "check_required",
                    "message": "Run check_pto_request for these dates first, show the result, and ask the "
                               "employee to confirm."}
        if not caller.invocation_id or check["invocation_id"] == caller.invocation_id:
            await _run(repo.record_event, event_type="CONFIRMATION_MISSING",
                       details={"reason": "same turn as check"}, **event)
            return {"status": "confirmation_required",
                    "message": "The employee has not confirmed yet. Show the check result and wait for them to "
                               "confirm in their next message before submitting."}

        evaluation, position = await _evaluate(caller, me, start, end)
        if not evaluation.eligible:
            await _run(repo.record_event, event_type="REJECTED", eligible=False,
                       details=evaluation.as_dict(), **event)
            return {"status": "rejected", **evaluation.as_dict(),
                    "message": "Conditions changed since the check; the request no longer passes."}

        governing = evaluation.governing_editions[0]
        request_id, inserted = await _run(
            repo.insert_request_once,
            idempotency_key=key, employee_id=emp, approver_id=position["manager_id"], start=start, end=end,
            days=evaluation.business_days, reason=reason.strip()[:200], governing_doc_id=governing["doc_id"],
            submitted_by=caller.email, session_id=caller.session_id, invocation_id=caller.invocation_id,
            submitted_date=config.today(),
        )
        if not inserted:
            existing = await _run(repo.active_request_by_key, key)
            await _run(repo.record_event, event_type="DUPLICATE",
                       request_id=existing and existing["request_id"], **event)
            return {"status": "already_submitted", "request": existing,
                    "message": "A request for these exact dates already exists. No new record was created."}

        await _run(repo.record_event, event_type="SUBMITTED", eligible=True, request_id=request_id,
                   details=evaluation.as_dict(), **event)
        return {
            "status": "submitted",
            "request_id": request_id,
            "employee_id": emp,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "business_days": evaluation.business_days,
            "approver_id": position["manager_id"],
            "request_status": "pending",
            "governing_edition": governing,
            "warnings": evaluation.warnings,
        }


@mcp.custom_route("/health", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


app = mcp.streamable_http_app()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="streamable-http")
