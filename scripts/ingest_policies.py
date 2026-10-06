"""Upload the policy corpus to Cloud Storage and (re)import it into Vertex AI Search with edition metadata.

    python -m scripts.ingest_policies --source-dir ../TakeHomeDocs

Steps:
  1. Upload each document listed in config/policy_editions.json to the bucket.
  2. Make sure the data store schema has every metadata field as filterable
     (indexable) and returned with results (retrievable).
  3. Write metadata.jsonl (one line per document: id, structData, content URI)
     and import it, replacing whatever the data store held before.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as de
from google.cloud import storage

ROOT = Path(__file__).resolve().parent.parent
MIME = {".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
FIELDS = ["doc_id", "title", "policy_type", "plan_year", "edition_status", "versioned",
          "effective_start", "effective_end", "governs"]
FILTERABLE = {"doc_id", "policy_type", "plan_year", "edition_status", "versioned"}


def struct_data(e: dict) -> dict:
    # Strings throughout, so every field can be used with the ANY("...") filter syntax.
    return {k: "" if e[k] is None else str(e[k]).lower() if isinstance(e[k], bool) else str(e[k]) for k in FIELDS}


def schema_json() -> str:
    props = {
        f: {"type": "string", "retrievable": True, "indexable": f in FILTERABLE,
            "dynamicFacetable": f in FILTERABLE,
            "searchable": f in {"governs", "policy_type", "plan_year"}}
        for f in FIELDS
    }
    # A key property may only be retrievable.
    props["title"] = {"type": "string", "retrievable": True, "keyPropertyMapping": "title"}
    return json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                       "properties": props})


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-dir", required=True, type=Path)
    p.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"))
    p.add_argument("--bucket", default=os.getenv("POLICY_BUCKET", "meridian-dynamics-policies-corpus"))
    p.add_argument("--location", default=os.getenv("VERTEX_SEARCH_LOCATION", "global"))
    p.add_argument("--data-store", default=os.getenv("VERTEX_DATA_STORE_ID"))
    args = p.parse_args()

    editions = json.loads((ROOT / "config" / "policy_editions.json").read_text())["editions"]
    bucket = storage.Client(project=args.project).bucket(args.bucket)

    print(f"Uploading {len(editions)} documents to gs://{args.bucket}/policies/ ...")
    lines = []
    for e in editions:
        src = args.source_dir / e["file_name"]
        blob = bucket.blob(f"policies/{e['file_name']}")
        blob.upload_from_filename(str(src), content_type=MIME[src.suffix])
        lines.append(json.dumps({
            "id": e["doc_id"],
            "structData": struct_data(e),
            "content": {"mimeType": MIME[src.suffix], "uri": f"gs://{args.bucket}/{blob.name}"},
        }))
    manifest = bucket.blob("policies/metadata.jsonl")
    manifest.upload_from_string("\n".join(lines) + "\n", content_type="application/json")

    opts = None if args.location == "global" else ClientOptions(
        api_endpoint=f"{args.location}-discoveryengine.googleapis.com")
    store = (f"projects/{args.project}/locations/{args.location}/collections/default_collection"
             f"/dataStores/{args.data_store}")

    print("Updating data store schema ...")
    de.SchemaServiceClient(client_options=opts).update_schema(
        request=de.UpdateSchemaRequest(schema=de.Schema(name=f"{store}/schemas/default_schema",
                                                        json_schema=schema_json()))
    ).result(timeout=600)

    print("Importing documents (FULL reconciliation) ...")
    op = de.DocumentServiceClient(client_options=opts).import_documents(
        request=de.ImportDocumentsRequest(
            parent=f"{store}/branches/default_branch",
            gcs_source=de.GcsSource(input_uris=[f"gs://{args.bucket}/{manifest.name}"], data_schema="document"),
            reconciliation_mode=de.ImportDocumentsRequest.ReconciliationMode.FULL,
        )
    )
    result = op.result(timeout=1800)
    print(f"Import finished. Errors: {list(result.error_samples) or 'none'}")


if __name__ == "__main__":
    main()
