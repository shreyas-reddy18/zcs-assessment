"""MCP server configuration. Everything environment-specific comes from env vars."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    _COMPANY_TZ = ZoneInfo(os.getenv("COMPANY_TIMEZONE", "America/Chicago"))
except Exception:  # tz database missing (e.g. bare Windows without tzdata)
    _COMPANY_TZ = timezone(timedelta(hours=-6))

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
BQ_DATASET = os.getenv("BQ_DATASET", "meridian_dynamics_operations")

# A PTO check only counts as confirmed if the employee replied in a later turn
# of the same conversation within this window.
CONFIRMATION_WINDOW_MINUTES = int(os.getenv("CONFIRMATION_WINDOW_MINUTES", "30"))

# Optional fixed "today" (YYYY-MM-DD). The provided dataset is anchored in
# spring 2026; pinning the date keeps a demo consistent with it. Unset = real date.
AS_OF_DATE = os.getenv("AS_OF_DATE", "").strip()


def table(name: str) -> str:
    """Fully qualified, backtick-quoted BigQuery table reference."""
    return f"`{PROJECT_ID}.{BQ_DATASET}.{name}`"


def today() -> date:
    if AS_OF_DATE:
        return date.fromisoformat(AS_OF_DATE)
    return datetime.now(_COMPANY_TZ).date()
