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

def execute_query(query: str, params: dict = None) -> list[dict]:
    """
    Executes a parameterized SQL query safely against BigQuery.
    
    Args:
        query: The SQL query string with parameter placeholders (e.g., @email).
        params: Dictionary of parameters to substitute into the query.
        
    Returns:
        List of dictionaries representing the rows returned.
    """
    client = get_bigquery_client()
    
    job_config = bigquery.QueryJobConfig()
    if params:
        query_params = []
        for key, value in params.items():
            if isinstance(value, int):
                query_params.append(bigquery.ScalarQueryParameter(key, "INT64", value))
            elif isinstance(value, float):
                query_params.append(bigquery.ScalarQueryParameter(key, "FLOAT64", value))
            elif isinstance(value, bool):
                query_params.append(bigquery.ScalarQueryParameter(key, "BOOL", value))
            else:
                query_params.append(bigquery.ScalarQueryParameter(key, "STRING", str(value)))
        job_config.query_parameters = query_params

    query_job = client.query(query, job_config=job_config)
    results = query_job.result()
    return [dict(row) for row in results]
