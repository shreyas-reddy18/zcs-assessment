import os
import vertexai
from vertexai.preview import reasoning_engines

class HRAgent:
    def __init__(self):
        self.model = None
        self.chat = None
        self.system_instruction = (
            "You are an internal HR assistant. Your role is to answer questions about HR policies and assist employees with PTO requests. "
            "When answering questions based on company policy, you MUST synthesize your answers by explicitly citing your sources. "
            "You MUST explicitly name the policy edition/year you relied on (e.g., 'According to the 2024 Employee Handbook...'). "
            "Under NO circumstances should you blend figures, rules, or data from two different editions into one answer. "
            "Always require explicit confirmation before submitting a PTO request. "
            "You will be provided with the user's `auth_token` in the initial prompt. You must pass this exact `auth_token` to every tool you call. "
            "If a user asks a policy question but does not specify a year (e.g., 'What is the parental leave policy?'), you must ask them to clarify which year they are asking about before answering. Do not guess or assume a default year. "
            "When a user asks to submit a PTO request, you must follow a strict three-step process: 1. Validate the request against the governing policy using the validation tool. 2. Surface the results of this validation check to the user. 3. Ask for explicit confirmation to proceed only after showing them the validation results. If they confirm, execute the final submission tool exactly once. "
            "If a tool returns an unauthorized error or indicates you do not have permission to view a specific employee's record, you must politely inform the user that they do not have the required access tier to view that information. "
        )

    def set_up(self):
        """Runs on container boot. Must be lightweight and error-free."""
        pass

    def query(self, input: str, auth_token: str) -> dict:
        """Lazy-initializes the model on first query to prevent container startup crashes."""
        if self.chat is None:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            
            vertexai.init(
                project="zcs-fde-assessment-shreyas", 
                location="us-central1"
            )
            
            tools_list = [
                get_employee_id,
                get_personal_record,
                get_direct_reports,
                get_pending_requests,
                validate_pto_request,
                submit_pto_request,
                search_hr_policy
            ]
            
            self.model = GenerativeModel(
                model_name="gemini-1.5-pro-001",
                system_instruction=self.system_instruction,
                tools=tools_list
            )
            self.chat = self.model.start_chat()

        contextualized_input = f"[User Auth Token: {auth_token}]\n\n{input}"
        response = self.chat.send_message(contextualized_input)
        return {"response": response.text}

# ==============================================================================
# TOOL DEFINITIONS (HTTP & RAG WRAPPERS)
# ==============================================================================

def get_employee_id(auth_token: str) -> dict:
    """Looks up the current user's employee_id given their auth token."""
    import requests
    url = "https://zcs-assessment-782492088107.us-central1.run.app/api/tools/get_employee_id"
    headers = {"Authorization": f"Bearer {auth_token}"}
    response = requests.post(url, headers=headers)
    return response.json()

def get_personal_record(target_employee_id: str, auth_token: str) -> dict:
    """Fetches PTO balance, YTD usage, and employee details for a specific employee."""
    import requests
    url = "https://zcs-assessment-782492088107.us-central1.run.app/api/tools/get_personal_record"
    headers = {"Authorization": f"Bearer {auth_token}"}
    payload = {"target_employee_id": target_employee_id}
    response = requests.post(url, json=payload, headers=headers)
    return response.json()

def get_direct_reports(auth_token: str) -> dict:
    """Retrieves the list and PTO details of all direct reports for the authenticated manager."""
    import requests
    url = "https://zcs-assessment-782492088107.us-central1.run.app/api/tools/get_direct_reports"
    headers = {"Authorization": f"Bearer {auth_token}"}
    response = requests.post(url, headers=headers)
    return response.json()

def get_pending_requests(target_employee_id: str, auth_token: str) -> dict:
    """Fetches pending and approved leave requests from the pto_requests table."""
    import requests
    url = "https://zcs-assessment-782492088107.us-central1.run.app/api/tools/get_pending_requests"
    headers = {"Authorization": f"Bearer {auth_token}"}
    payload = {"target_employee_id": target_employee_id}
    response = requests.post(url, json=payload, headers=headers)
    return response.json()

def validate_pto_request(target_employee_id: str, start_date: str, end_date: str, days_requested: int, required_lead_time_days: int, blackout_periods: list, auth_token: str) -> dict:
    """Step 1 of PTO submission: Validates a PTO request against corporate policy."""
    import requests
    url = "https://zcs-assessment-782492088107.us-central1.run.app/api/tools/validate_pto_request"
    headers = {"Authorization": f"Bearer {auth_token}"}
    payload = {
        "target_employee_id": target_employee_id,
        "start_date": start_date,
        "end_date": end_date,
        "days_requested": days_requested,
        "required_lead_time_days": required_lead_time_days,
        "blackout_periods": blackout_periods
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.json()

def submit_pto_request(target_employee_id: str, start_date: str, end_date: str, days_requested: int, request_type: str, user_confirmed: bool, auth_token: str) -> dict:
    """Step 2 of PTO submission: Idempotently writes the PTO request to the database."""
    import requests
    url = "https://zcs-assessment-782492088107.us-central1.run.app/api/tools/submit_pto_request"
    headers = {"Authorization": f"Bearer {auth_token}"}
    payload = {
        "target_employee_id": target_employee_id,
        "start_date": start_date,
        "end_date": end_date,
        "days_requested": days_requested,
        "request_type": request_type,
        "user_confirmed": user_confirmed
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.json()

def search_hr_policy(query: str) -> str:
    """Searches the HR company policies (Data Store) to answer policy questions."""
    from google.cloud import discoveryengine_v1 as discoveryengine
    
    project_id = "zcs-fde-assessment-shreyas"
    location = "global" 
    data_store_id = "meridian-policy-data-store_1790612128753" 
    
    client = discoveryengine.SearchServiceClient()
    serving_config = client.serving_config_path(
        project=project_id,
        location=location,
        data_store=data_store_id,
        serving_config="default_config",
    )
    
    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=3,
    )
    
    response = client.search(request)
    snippets = []
    for result in response.results:
        document = result.document
        struct_data = document.derived_struct_data
        if "snippets" in struct_data:
            for snippet in struct_data["snippets"]:
                snippets.append(snippet.get("snippet", ""))
                
    if not snippets:
        return "No relevant policy documents found."
        
    return "\n\n".join(snippets)

# ==============================================================================
# DEPLOYMENT SCRIPT
# ==============================================================================
if __name__ == "__main__":
    vertexai.init(
        project="zcs-fde-assessment-shreyas", 
        location="us-central1", 
        staging_bucket="gs://meridian-dynamics-policies-corpus"
    )
    
    print("Deploying Lazy-Loading Reasoning Engine...")
    
    remote_app = reasoning_engines.ReasoningEngine.create(
        reasoning_engine=HRAgent(),
        display_name="HR-Conversational-Knowledge-Agent",
        description="Lazy-loaded HR Assistant orchestrating tools and Discovery Engine",
        requirements=[
            "google-cloud-aiplatform",
            "google-cloud-discoveryengine",
            "requests"
        ],
        extra_packages=[], 
    )
    
    print("==================================================")
    print("🚀 Deployment Successful!")
    print(f"Resource Name: {remote_app.resource_name}")
    print("==================================================")