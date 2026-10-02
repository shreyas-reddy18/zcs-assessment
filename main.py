import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List

from auth import get_current_user_email, get_user_identity
import tools

# Load environment variables from .env file
load_dotenv()

# Initialize FastAPI app
app = FastAPI(
    title="HR Tool Execution API",
    description="Backend tool-executor for Vertex AI Reasoning Engine querying BigQuery with OAuth 2.0",
    version="0.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For dev purposes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    """Basic health-check endpoint to verify the server is running."""
    return {
        "status": "healthy",
        "project_id": os.getenv("GCP_PROJECT_ID", "Not configured")
    }

@app.get("/auth/verify")
async def verify_auth(email: str = Depends(get_current_user_email)):
    """
    Endpoint that accepts a Google OAuth 2.0 ID token (via Authorization header as Bearer token),
    verifies it, and returns the user's mapped identity info from BigQuery.
    """
    identity = get_user_identity(email)
    return {
        "email": email,
        "identity": identity
    }

# --- Pydantic Models for Tool Requests ---

class GetPersonalRecordRequest(BaseModel):
    target_employee_id: str

class GetPendingRequestsRequest(BaseModel):
    target_employee_id: str

class ValidatePTORequest(BaseModel):
    target_employee_id: str
    start_date: str
    end_date: str
    days_requested: int
    required_lead_time_days: int
    blackout_periods: List[str]

class SubmitPTORequest(BaseModel):
    target_employee_id: str
    start_date: str
    end_date: str
    days_requested: int
    request_type: str
    user_confirmed: bool

# --- Tool Execution Endpoints ---

@app.post("/api/tools/get_employee_id")
async def api_get_employee_id(email: str = Depends(get_current_user_email)):
    try:
        employee_id = tools.get_employee_id_by_email(email)
        return {"employee_id": employee_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tools/get_personal_record")
async def api_get_personal_record(request: GetPersonalRecordRequest, email: str = Depends(get_current_user_email)):
    try:
        result = tools.get_personal_record(request.target_employee_id, email)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tools/get_direct_reports")
async def api_get_direct_reports(email: str = Depends(get_current_user_email)):
    try:
        result = tools.get_direct_reports(email)
        return {"direct_reports": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tools/get_pending_requests")
async def api_get_pending_requests(request: GetPendingRequestsRequest, email: str = Depends(get_current_user_email)):
    try:
        result = tools.get_pending_requests(request.target_employee_id, email)
        return {"pending_requests": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tools/validate_pto_request")
async def api_validate_pto_request(request: ValidatePTORequest, email: str = Depends(get_current_user_email)):
    try:
        result = tools.validate_pto_request(
            target_employee_id=request.target_employee_id,
            current_user_email=email,
            start_date=request.start_date,
            end_date=request.end_date,
            days_requested=request.days_requested,
            required_lead_time_days=request.required_lead_time_days,
            blackout_periods=request.blackout_periods
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tools/submit_pto_request")
async def api_submit_pto_request(request: SubmitPTORequest, email: str = Depends(get_current_user_email)):
    try:
        result = tools.submit_pto_request(
            target_employee_id=request.target_employee_id,
            current_user_email=email,
            start_date=request.start_date,
            end_date=request.end_date,
            days_requested=request.days_requested,
            request_type=request.request_type,
            user_confirmed=request.user_confirmed
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
