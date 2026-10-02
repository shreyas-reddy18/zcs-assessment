import os
from dotenv import load_dotenv
import vertexai
from vertexai import agent_engines
from agent import hr_assistant

# Load environment variables
load_dotenv()

def deploy():
    project_id = os.getenv("GCP_PROJECT_ID")
    location = os.getenv("GCP_REGION", "us-central1")
    
    if not project_id:
        raise ValueError("GCP_PROJECT_ID must be set in the environment.")
        
    print(f"Initializing Vertex AI in project {project_id}, region {location}...")
    vertexai.init(project=project_id, location=location)
    
    print("Wrapping ADK agent in AdkApp...")
    # Wrap the agent.py hr_assistant in the AdkApp deployment wrapper
    app = agent_engines.AdkApp(agent=hr_assistant)
    
    print("Deploying to Vertex AI Agent Engine...")
    remote_app = agent_engines.ReasoningEngine.create(
        app,
        display_name="Meridian HR Assistant",
        description="Conversational HR Assistant powered by ADK and Gemini 3.8 Flash",
        # Dependencies to package with the deployment
        requirements=[
            "google-adk",
            "mcp",
            "google-cloud-aiplatform",
            "python-dotenv"
        ]
    )
    
    print(f"Deployment complete! Reasoning Engine ID: {remote_app.resource_name}")

if __name__ == "__main__":
    deploy()
