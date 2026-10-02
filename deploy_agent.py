import os
from dotenv import load_dotenv
import vertexai
from vertexai import agent_engines
from vertexai.preview import reasoning_engines
from agent import hr_assistant

# Load environment variables
load_dotenv()

def deploy():
    project_id = os.getenv("GCP_PROJECT_ID")
    location = os.getenv("GCP_REGION", "us-central1")
    
    if not project_id:
        raise ValueError("GCP_PROJECT_ID must be set in the environment.")
        
    print(f"Initializing Vertex AI in project {project_id}, region {location}...")
    vertexai.init(
        project=project_id, 
        location=location,
        staging_bucket="gs://meridian-dynamics-policies-corpus" # Added your bucket from the previous script
    )
    
    print("Wrapping ADK agent in AdkApp...")
    app = agent_engines.AdkApp(agent=hr_assistant)
    
    print("Deploying to Vertex AI Agent Engine...")
    remote_app = reasoning_engines.ReasoningEngine.create(
        reasoning_engine=app,
        display_name="Meridian HR Assistant",
        description="Conversational HR Assistant powered by ADK and Gemini 1.5 Pro",
        requirements=[
            "google-adk>=2.10.0",
            "mcp<2.0.0",
            "google-cloud-aiplatform",
            "python-dotenv"
        ]
    )
    
    print("==================================================")
    print("🚀 Deployment Successful!")
    print(f"Resource Name: {remote_app.resource_name}")
    print("==================================================")

if __name__ == "__main__":
    deploy()