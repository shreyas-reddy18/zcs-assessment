"""(Re)build the BigQuery warehouse from the provided CSVs and the policy config.

    python -m scripts.load_bigquery --source-dir ../TakeHomeDocs --demo-email you@gmail.com

Recreates every table except the audit log (pto_request_events), so it also
resets any PTO requests the assistant has written. Safe to rerun.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parent.parent
REPLACED_TABLES = [  # dependents first, so foreign keys never point at a dropped table
    "non_working_days", "pto_blackout_periods", "pto_lead_time_rules", "policy_editions",
    "pto_requests", "identity_map", "employees",
]


def run_sql_file(client: bigquery.Client, path: Path, ds: str) -> None:
    sql = path.read_text(encoding="utf-8").replace("${DS}", ds)
    client.query(sql).result()


def load_csv_rows(client: bigquery.Client, ds: str, table: str, rows: list[dict]) -> None:
    """Load rows into an existing typed table (schema comes from the DDL)."""
    target = client.get_table(f"{ds.strip('`')}.{table}")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[f.name for f in target.schema])
    writer.writeheader()
    writer.writerows(rows)
    job = client.load_table_from_file(
        io.BytesIO(buf.getvalue().encode()),
        target,
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV, skip_leading_rows=1,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    job.result()
    print(f"  {table}: {job.output_rows} rows")


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-dir", required=True, type=Path, help="Folder with employees.csv, pto_requests.csv, identity_map.csv")
    p.add_argument("--demo-email", help="Bind this Google account to the first identity_map row (E002)")
    p.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"))
    p.add_argument("--dataset", default=os.getenv("BQ_DATASET", "meridian_dynamics_operations"))
    p.add_argument("--location", default=os.getenv("BQ_LOCATION", "us-central1"))
    args = p.parse_args()

    client = bigquery.Client(project=args.project)
    ds = f"`{args.project}.{args.dataset}`"
    dataset = bigquery.Dataset(f"{args.project}.{args.dataset}")
    dataset.location = args.location
    client.create_dataset(dataset, exists_ok=True)

    print("Dropping derived tables ...")
    for t in REPLACED_TABLES:
        client.delete_table(f"{args.project}.{args.dataset}.{t}", not_found_ok=True)
    print("Creating tables ...")
    run_sql_file(client, ROOT / "sql" / "01_tables.sql", ds)

    print("Loading data ...")
    employees = read_csv(args.source_dir / "employees.csv")
    load_csv_rows(client, ds, "employees", employees)

    identity = read_csv(args.source_dir / "identity_map.csv")
    if args.demo_email:
        identity[0]["google_email"] = args.demo_email.strip().lower()
        identity[0]["notes"] = f"Demo account bound to {identity[0]['employee_id']}."
    load_csv_rows(client, ds, "identity_map", identity)

    requests = [{**r, "source": "hris_import"} for r in read_csv(args.source_dir / "pto_requests.csv")]
    load_csv_rows(client, ds, "pto_requests", requests)

    editions = json.loads((ROOT / "config" / "policy_editions.json").read_text())["editions"]
    load_csv_rows(client, ds, "policy_editions", [
        {**e, "versioned": str(e["versioned"]).lower(), "effective_end": e["effective_end"] or ""}
        for e in editions
    ])

    rules = json.loads((ROOT / "config" / "pto_policy_rules.json").read_text())["plan_years"]
    leads, blackouts, off = [], [], []
    for year, r in rules.items():
        for x in r["lead_time_rules"]:
            leads.append({"plan_year": year, "min_business_days": x["min_business_days"],
                          "max_business_days": x["max_business_days"] if x["max_business_days"] is not None else "",
                          "min_notice_hours": x["min_notice_hours"], "note": x.get("note", ""),
                          "source_doc_id": r["source_doc_id"], "source_section": x["section"]})
        for x in r["blackout_periods"]:
            blackouts.append({"plan_year": year, "name": x["name"], "start_date": x["start_date"],
                              "end_date": x["end_date"], "max_days_allowed": x["max_days_allowed"],
                              "source_doc_id": r["source_doc_id"], "source_section": x["section"]})
        for x in r["non_working_days"]:
            off.append({"day": x["date"], "plan_year": year, "name": x["name"], "kind": x["kind"],
                        "source_doc_id": r["source_doc_id"]})
    load_csv_rows(client, ds, "pto_lead_time_rules", leads)
    load_csv_rows(client, ds, "pto_blackout_periods", blackouts)
    load_csv_rows(client, ds, "non_working_days", off)

    print("Creating access function and views ...")
    run_sql_file(client, ROOT / "sql" / "02_access.sql", ds)
    print(f"Done: {args.project}.{args.dataset}")


if __name__ == "__main__":
    main()
