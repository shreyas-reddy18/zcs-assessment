"""
Authentication and Authorization module.
Handles Google Sign-In token verification and identity map logic.
"""
from google.oauth2 import id_token
from google.auth.transport import requests
import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from db import execute_query

security = HTTPBearer()

def verify_oauth_token(token: str) -> dict:
    """
    Verifies a Google OAuth 2.0 token and returns the decoded token payload.
    """
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    try:
        # Verify the token with Google
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), client_id)
        return idinfo
    except ValueError as e:
        # Invalid token
        raise Exception(f"Token verification failed: {e}")

def get_current_user_email(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    """
    FastAPI dependency to extract and verify the OAuth token from the Authorization header.
    Returns the user's verified email.
    """
    token = credentials.credentials
    try:
        idinfo = verify_oauth_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))
        
    email = idinfo.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Token does not contain an email address")
    return email

def get_user_identity(email: str) -> dict:
    """
    Queries the identity_map table to retrieve the user's employee_id and access_tier.
    """
    dataset = os.getenv("BQ_DATASET", "hr_dataset")
    query = f"""
        SELECT employee_id, access_tier
        FROM `{dataset}.identity_map`
        WHERE email = @email
    """
    results = execute_query(query, {"email": email})
    if not results:
        raise HTTPException(status_code=403, detail="User not found in identity map")
    return results[0]

def authorize_access(current_user_email: str, target_employee_id: str) -> bool:
    """
    Authorization Gatekeeper.
    Enforces strict access control based on access_tier and manager relationships.
    """
    identity = get_user_identity(current_user_email)
    access_tier = identity.get("access_tier")
    current_employee_id = str(identity.get("employee_id"))
    target_employee_id = str(target_employee_id)

    if access_tier == "people_ops":
        # Full access to all records
        return True
        
    if access_tier == "employee":
        if current_employee_id != target_employee_id:
            raise HTTPException(
                status_code=403, 
                detail="Access Denied: Employees can only access their own records."
            )
        return True
        
    if access_tier == "manager":
        if current_employee_id == target_employee_id:
            return True
            
        # Check if the current user is the manager of the target employee
        dataset = os.getenv("BQ_DATASET", "hr_dataset")
        query = f"""
            SELECT manager_id
            FROM `{dataset}.employees`
            WHERE employee_id = @target_employee_id
        """
        results = execute_query(query, {"target_employee_id": target_employee_id})
        if not results:
            raise HTTPException(status_code=404, detail="Target employee not found")
            
        manager_id = str(results[0].get("manager_id"))
        if manager_id != current_employee_id:
            raise HTTPException(
                status_code=403, 
                detail="Access Denied: Managers can only access their own records or records of their direct reports."
            )
        return True

    raise HTTPException(status_code=403, detail=f"Unknown access tier: {access_tier}")
