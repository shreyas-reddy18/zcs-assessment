"""Thin BigQuery wrapper: one client, parameterized queries only."""
from __future__ import annotations

import functools
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from google.cloud import bigquery

from . import config


@functools.lru_cache(maxsize=1)
def client() -> bigquery.Client:
    return bigquery.Client(project=config.PROJECT_ID or None)


def _param(name: str, value: Any) -> bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter:
    # A (type, value) tuple sets the type explicitly, which NULLs need.
    if isinstance(value, tuple):
        type_, value = value
        if type_ == "JSON" and value is not None:
            value = json.dumps(value, default=str)
        return bigquery.ScalarQueryParameter(name, type_, value)
    if isinstance(value, list):
        return bigquery.ArrayQueryParameter(name, "STRING", [str(v) for v in value])
    # bool before int: bool is a subclass of int.
    if isinstance(value, bool):
        return bigquery.ScalarQueryParameter(name, "BOOL", value)
    if isinstance(value, int):
        return bigquery.ScalarQueryParameter(name, "INT64", value)
    if isinstance(value, Decimal):
        return bigquery.ScalarQueryParameter(name, "NUMERIC", value)
    if isinstance(value, datetime):
        return bigquery.ScalarQueryParameter(name, "TIMESTAMP", value)
    if isinstance(value, date):
        return bigquery.ScalarQueryParameter(name, "DATE", value)
    return bigquery.ScalarQueryParameter(name, "STRING", value)


def query(sql: str, **params: Any) -> list[dict]:
    job_config = bigquery.QueryJobConfig(query_parameters=[_param(k, v) for k, v in params.items()])
    job = client().query(sql, job_config=job_config)
    rows = [dict(r) for r in job.result()]
    if job.statement_type in ("INSERT", "MERGE", "UPDATE", "DELETE"):
        return [{"num_dml_affected_rows": job.num_dml_affected_rows or 0}]
    return rows
