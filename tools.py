"""
Model Context Protocol (MCP) tool definitions.
Exposes specific functions that the HR Conversational Knowledge Agent can call.
"""
import os
import uuid
import sys
from datetime import datetime, date
from mcp.server.mcpserver import MCPServer
from auth import authorize_access
from db import execute_query
from dotenv import load_dotenv

# Load environment variables for the MCP Server process
load_dotenv()

# Initialize MCP server instance
mcp = MCPServer("HR-Agent-Tools")

@mcp.tool()
def get_employee_id_by_email(email: str) -> str:
    """
    Looks up an employee_id given their Google email address.
    Use this to find the current user's employee ID.
    """
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    query = f"SELECT employee_id FROM `{dataset}.identity_map` WHERE google_email = @email"
    results = execute_query(query, {"email": email})
    if not results:
        return "Employee ID not found for this email."
    return str(results[0]["employee_id"])

@mcp.tool()
def get_personal_record(target_employee_id: str, current_user_email: str) -> dict:
    """
    Fetches PTO balance, YTD usage, and employee details.
    Enforces authorization boundaries based on the current user's email.
    """
    # Enforce authorization
    authorize_access(current_user_email, target_employee_id)
    
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    query = f"""
        SELECT employee_id, full_name, department, pto_balance_days, pto_used_ytd
        FROM `{dataset}.employees`
        WHERE employee_id = @employee_id
    """
    results = execute_query(query, {"employee_id": target_employee_id})
    if not results:
        return {"error": "Employee not found"}
    return results[0]

@mcp.tool()
def get_pending_requests(target_employee_id: str, current_user_email: str) -> list[dict]:
    """
    Fetches pending and approved leave requests from the pto_requests table.
    Enforces authorization boundaries based on the current user's email.
    """
    # Enforce authorization
    authorize_access(current_user_email, target_employee_id)
    
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    query = f"""
        SELECT request_id, start_date, end_date, status, request_type
        FROM `{dataset}.pto_requests`
        WHERE employee_id = @employee_id AND status IN ('pending', 'approved')
    """
    results = execute_query(query, {"employee_id": target_employee_id})
    return results

def validate_pto_policy(start_date_str: str, end_date_str: str, days_requested: int, current_balance: int) -> dict:
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
    
    if days_requested <= 2:
        if lead_time_days < 2:
            return {"passed": False, "reason": "Minimum 48 hours notice required for 1-2 days of leave."}
    else:
        if lead_time_days < 14:
            return {"passed": False, "reason": "Minimum 2 weeks notice required for 3+ consecutive days of leave."}
            
        # Blackout period check (July 7-18, 2026)
        blackout_start = date(2026, 7, 7)
        blackout_end = date(2026, 7, 18)
        if max(start_date, blackout_start) <= min(end_date, blackout_end):
            return {"passed": False, "reason": "Requests exceeding 2 consecutive days fall within the Summer Product Launch blackout period."}
            
    return {"passed": True, "reason": "Policy checks passed."}

@mcp.tool()
def validate_pto_request(target_employee_id: str, current_user_email: str, start_date: str, end_date: str, days_requested: int) -> dict:
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
    
    validation = validate_pto_policy(start_date, end_date, days_requested, current_balance)
    
    if validation["passed"]:
        validation["summary"] = {
            "dates": f"{start_date} to {end_date}",
            "total_days": days_requested,
            "balance_impact": f"{current_balance} -> {current_balance - days_requested}",
            "action_required": "Please prompt the user to confirm these details before submitting."
        }
        
    return validation

@mcp.tool()
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
    
    # Idempotency Check: query for exact match pending/approved request
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
        
    # Re-verify balance just to be safe at write time
    balance_query = f"SELECT pto_balance_days FROM `{dataset}.employees` WHERE employee_id = @employee_id"
    balance_res = execute_query(balance_query, {"employee_id": target_employee_id})
    if not balance_res or balance_res[0]["pto_balance_days"] < days_requested:
         return {"error": "Write failed: Insufficient PTO balance."}
         
    # Execute atomic transaction
    request_id = str(uuid.uuid4())
    
    # BigQuery Multi-statement Transaction
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

if __name__ == "__main__":
    mcp.run()
