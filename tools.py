"""
Core HR tools.
Exposes specific functions for HR queries and transactions.
"""
import os
import uuid
import sys
from datetime import datetime, date
from auth import authorize_access
from db import execute_query
from dotenv import load_dotenv

load_dotenv()


def get_employee_id_by_email(email: str) -> str:
    """
    Looks up an employee_id given their Google email address.
    Use this to find the current user's employee ID.
    """
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    query = f"SELECT employee_id FROM `{dataset}.identity_map` WHERE google_email = @email"
    results = execute_query(query, {"email": email})
    if not results:
        raise ValueError(f"HTTP 403: User {email} not found in identity map. Access Denied.")
    return str(results[0]["employee_id"])


def get_personal_record(target_employee_id: str, current_user_email: str) -> dict:
    """
    Fetches PTO balance, YTD usage, and employee details for a specific employee.
    Enforces authorization: users can view their own record, managers can view direct reports,
    and People Ops can view all.
    """
    authorize_access(current_user_email, target_employee_id)
    
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    query = f"""
        SELECT employee_id, full_name, department, job_title, pto_balance_days, pto_used_ytd
        FROM `{dataset}.employees`
        WHERE employee_id = @employee_id
    """
    results = execute_query(query, {"employee_id": target_employee_id})
    if not results:
        raise ValueError(f"Target employee ID {target_employee_id} not found in the database.")
    return results[0]


def get_direct_reports(current_user_email: str) -> list[dict]:
    """
    Retrieves the list and PTO details of all direct reports for the authenticated manager.
    Returns an empty list if the user has no direct reports.
    """
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    
    # 1. Resolve manager's employee_id from email
    id_query = f"SELECT employee_id FROM `{dataset}.identity_map` WHERE google_email = @email"
    id_res = execute_query(id_query, {"email": current_user_email})
    if not id_res:
        return []
    manager_id = id_res[0]["employee_id"]
    
    # 2. Query all employees managed by this ID
    query = f"""
        SELECT employee_id, full_name, department, job_title, pto_balance_days, pto_used_ytd
        FROM `{dataset}.employees`
        WHERE manager_id = @manager_id
    """
    return execute_query(query, {"manager_id": manager_id})


def get_pending_requests(target_employee_id: str, current_user_email: str) -> list[dict]:
    """
    Fetches pending and approved leave requests from the pto_requests table.
    Enforces authorization boundaries based on the current user's email.
    """
    authorize_access(current_user_email, target_employee_id)
    
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    query = f"""
        SELECT request_id, employee_id, start_date, end_date, days_requested, status
        FROM `{dataset}.pto_requests`
        WHERE employee_id = @employee_id AND status IN ('pending', 'approved')
    """
    results = execute_query(query, {"employee_id": target_employee_id})
    return results

def validate_pto_policy(start_date_str: str, end_date_str: str, days_requested: int, current_balance: int, required_lead_time_days: int, blackout_periods: list[str]) -> dict:
    """Evaluates PTO request against corporate policy rules."""
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"passed": False, "reason": "Invalid date format. Use YYYY-MM-DD."}
        
    today = date.today()
    
    if days_requested <= 0:
        return {"passed": False, "reason": "Days requested must be greater than 0."}
        
    if days_requested > current_balance:
        return {"passed": False, "reason": f"Insufficient balance. Requested {days_requested}, but only have {current_balance}."}
        
    lead_time_days = (start_date - today).days
    
    if lead_time_days < required_lead_time_days:
        return {"passed": False, "reason": f"Minimum {required_lead_time_days} days notice required."}

    for bp in blackout_periods:
        try:
            if " to " in bp:
                b_start_str, b_end_str = bp.split(" to ")
                b_start = datetime.strptime(b_start_str.strip(), "%Y-%m-%d").date()
                b_end = datetime.strptime(b_end_str.strip(), "%Y-%m-%d").date()
            else:
                b_start = datetime.strptime(bp.strip(), "%Y-%m-%d").date()
                b_end = b_start
            
            if start_date <= b_end and end_date >= b_start:
                return {"passed": False, "reason": f"Requested dates overlap with blackout period: {bp}"}
        except ValueError:
            pass
            
    return {"passed": True, "reason": "Policy checks passed."}


def validate_pto_request(
    target_employee_id: str, 
    current_user_email: str, 
    start_date: str, 
    end_date: str, 
    days_requested: int,
    required_lead_time_days: int,
    blackout_periods: list[str]
) -> dict:
    """
    Step 1 of PTO submission: Validates a PTO request against corporate policy.
    The agent MUST present the summary returned by this tool to the user and prompt for explicit confirmation 
    before calling submit_pto_request.
    """
    authorize_access(current_user_email, target_employee_id)
    
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    query = f"SELECT pto_balance_days FROM `{dataset}.employees` WHERE employee_id = @employee_id"
    results = execute_query(query, {"employee_id": target_employee_id})
    
    if not results:
        return {"passed": False, "reason": "Employee not found."}
        
    current_balance = results[0]["pto_balance_days"]
    validation = validate_pto_policy(start_date, end_date, days_requested, current_balance, required_lead_time_days, blackout_periods)
    
    if validation["passed"]:
        validation["summary"] = {
            "dates": f"{start_date} to {end_date}",
            "total_days": days_requested,
            "balance_impact": f"{current_balance} -> {current_balance - days_requested}",
            "action_required": "Please prompt the user to confirm these details before submitting."
        }
        
    return validation


def submit_pto_request(
    target_employee_id: str, 
    current_user_email: str, 
    start_date: str, 
    end_date: str, 
    days_requested: int, 
    request_type: str, 
    user_confirmed: bool
) -> dict:
    """
    Step 2 of PTO submission: Idempotently writes the PTO request to the database.
    Requires user_confirmed=True after presenting the summary from validate_pto_request.
    """
    if not user_confirmed:
        return {"error": "Submission aborted. User confirmation is required."}
        
    authorize_access(current_user_email, target_employee_id)
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    
    check_query = f"""
        SELECT request_id, start_date, end_date, status
        FROM `{dataset}.pto_requests`
        WHERE employee_id = @employee_id 
          AND start_date = CAST(@start_date AS DATE)
          AND end_date = CAST(@end_date AS DATE)
          AND status IN ('pending', 'approved')
    """
    existing = execute_query(check_query, {
        "employee_id": target_employee_id,
        "start_date": start_date,
        "end_date": end_date
    })
    
    if existing:
        return {
            "status": "duplicate_found",
            "message": "A PTO request for these exact dates already exists.",
            "record": existing[0]
        }
        
    balance_query = f"SELECT pto_balance_days FROM `{dataset}.employees` WHERE employee_id = @employee_id"
    balance_res = execute_query(balance_query, {"employee_id": target_employee_id})
    if not balance_res or balance_res[0]["pto_balance_days"] < days_requested:
         return {"error": "Write failed: Insufficient PTO balance."}
         
    request_id = str(uuid.uuid4())
    
    transaction_sql = f"""
        BEGIN TRANSACTION;
        
        INSERT INTO `{dataset}.pto_requests` (request_id, employee_id, start_date, end_date, days_requested, status)
        VALUES (@request_id, @employee_id, CAST(@start_date AS DATE), CAST(@end_date AS DATE), @days_requested, 'pending');

        UPDATE `{dataset}.employees`
        SET pto_balance_days = pto_balance_days - @days_requested,
            pto_used_ytd = pto_used_ytd + @days_requested
        WHERE employee_id = @employee_id;
        
        COMMIT TRANSACTION;
    """
    
    try:
        execute_query(transaction_sql, {
            "request_id": request_id,
            "employee_id": target_employee_id,
            "start_date": start_date,
            "end_date": end_date,
            "days_requested": days_requested
        })
    except Exception as e:
        print(f"🚨 BIGQUERY WRITE ERROR: {e}", file=sys.stderr)
        return {"error": "Error: Database failure"}
        
    return {
        "status": "success",
        "message": "PTO request submitted successfully.",
        "request_id": request_id
    }