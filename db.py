"""
Database module for interacting with Google BigQuery.
Handles BigQuery client instantiation and executing queries.
"""
import os
from google.cloud import bigquery

def get_bigquery_client() -> bigquery.Client:
    """
    Initializes and returns a Google BigQuery client.
    Expects GCP credentials to be configured in the environment.
    """
    # Project ID can be explicitly provided or inferred from the environment
    project_id = os.getenv("GCP_PROJECT_ID")
    return bigquery.Client(project=project_id)

# TODO: Implement specific BigQuery SQL queries for HR data here.
