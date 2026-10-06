"""Retrieval over the Vertex AI Search data store that indexes the policy corpus.

Each document carries edition metadata as structData (see
config/policy_editions.json and scripts/ingest_policies.py): plan_year,
edition_status, versioned, effective dates, title. The model chooses the plan
year; this tool turns that choice into a data store filter, so superseded
editions are excluded by the search engine rather than by the model reading
dates off the page. Every excerpt comes back labelled with its edition.
"""
from __future__ import annotations

import functools
import time
from typing import Any

from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as de

from . import config

POLICY_TYPES = ("Handbook", "PTO_and_Leave", "Health_and_Benefits", "Expense_and_Reimbursement", "Code_of_Conduct")
_CATALOG_TTL_SECONDS = 600
_catalog_cache: tuple[float, list[dict]] | None = None


def _client_options() -> ClientOptions | None:
    if config.SEARCH_LOCATION == "global":
        return None
    return ClientOptions(api_endpoint=f"{config.SEARCH_LOCATION}-discoveryengine.googleapis.com")


@functools.lru_cache(maxsize=1)
def _search_client() -> de.SearchServiceClient:
    return de.SearchServiceClient(client_options=_client_options())


@functools.lru_cache(maxsize=1)
def _document_client() -> de.DocumentServiceClient:
    return de.DocumentServiceClient(client_options=_client_options())


def _edition(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": meta.get("doc_id"),
        "title": meta.get("title"),
        "policy_type": meta.get("policy_type"),
        "plan_year": meta.get("plan_year"),
        "edition_status": meta.get("edition_status"),
        "effective_start": meta.get("effective_start"),
        "effective_end": meta.get("effective_end") or None,
        "governs": meta.get("governs"),
        "single_edition_document": meta.get("versioned") == "false",
    }


def _catalog() -> list[dict]:
    """Editions as they exist in the data store, read from the documents' own metadata."""
    global _catalog_cache
    if _catalog_cache and time.monotonic() - _catalog_cache[0] < _CATALOG_TTL_SECONDS:
        return _catalog_cache[1]
    parent = f"{config.data_store_path()}/branches/default_branch"
    editions = [_edition(dict(d.struct_data)) for d in _document_client().list_documents(parent=parent)]
    editions.sort(key=lambda e: (e["policy_type"] or "", e["plan_year"] or ""))
    _catalog_cache = (time.monotonic(), editions)
    return editions


def _edition_filter(catalog: list[dict], plan_year: int, policy_type: str) -> str:
    """Data store filter that admits exactly the governing edition of each policy.

    Built from the catalog as (policy_type, plan_year) pairs: the requested year
    for versioned policies, the current edition when no year is given, and the
    only edition of single-edition documents either way.
    """
    if not catalog:  # catalog unavailable: fall back to the status flags on the documents
        clause = f'plan_year: ANY("{plan_year}")' if plan_year else 'edition_status: ANY("current")'
        return f'{clause} AND policy_type: ANY("{policy_type}")' if policy_type else clause
    pairs = {
        (e["policy_type"], e["plan_year"]) for e in catalog
        if (not policy_type or e["policy_type"] == policy_type)
        and (e["single_edition_document"]
             or (e["plan_year"] == str(plan_year) if plan_year else e["edition_status"] == "current"))
    }
    return " OR ".join(f'(policy_type: ANY("{t}") AND plan_year: ANY("{y}"))' for t, y in sorted(pairs))


def list_policy_editions() -> dict:
    """List every policy document edition in the corpus with its plan year and status (current or superseded).

    Use this to see which plan years exist before answering a question about a
    specific year, or to explain which edition governs.
    """
    try:
        return {"status": "ok", "editions": _catalog()}
    except Exception as e:  # surface, don't crash the turn
        return {"status": "error", "message": f"Could not read the policy catalog: {e}"}


def search_policy_documents(query: str, plan_year: int = 0, policy_type: str = "") -> dict:
    """Search Meridian Dynamics policy documents, restricted to one edition (plan year).

    Policies are reissued each year and editions disagree, so always choose the
    edition that governs the question:
      * Leave is governed by the year the leave is taken; expenses by the year
        incurred; benefits by the plan year.
      * If the question gives no date or year, pass plan_year=0 to search the
        current editions only.
      * To describe how a rule changed, call this once per year and keep the
        results separate.

    Args:
        query: What to look for, in plain words, e.g. "parental leave weeks for birthing parent".
        plan_year: The governing plan year, e.g. 2025 or 2026. 0 = current editions.
        policy_type: Optional filter, one of Handbook, PTO_and_Leave, Health_and_Benefits,
            Expense_and_Reimbursement, Code_of_Conduct.

    Returns:
        Excerpts, each labelled with document title, plan year, edition status, and
        page, for citation. status "no_edition" means no edition exists for that year.
    """
    if policy_type and policy_type not in POLICY_TYPES:
        return {"status": "invalid_input", "message": f"policy_type must be one of {', '.join(POLICY_TYPES)}."}

    try:
        catalog = _catalog()
    except Exception:
        catalog = []

    if plan_year and catalog:
        years = sorted({e["plan_year"] for e in catalog
                        if not e["single_edition_document"] and (not policy_type or e["policy_type"] == policy_type)})
        if str(plan_year) not in years:
            return {
                "status": "no_edition",
                "message": f"No {plan_year} edition of {policy_type or 'any versioned policy'} is in the corpus. "
                           f"Editions available: {', '.join(years)}. Say that this year's rules cannot be "
                           "determined rather than applying another year's figures.",
            }

    if plan_year:
        selection = f"Filtered to the {plan_year} editions (plus single-edition documents such as the Code of Conduct)."
    else:
        selection = "No year given: searched current editions only. Superseded editions were excluded."

    spec = de.SearchRequest.ContentSearchSpec
    request = de.SearchRequest(
        serving_config=f"{config.data_store_path()}/servingConfigs/default_config",
        query=query,
        filter=_edition_filter(catalog, plan_year, policy_type),
        page_size=4,
        content_search_spec=spec(
            extractive_content_spec=spec.ExtractiveContentSpec(max_extractive_segment_count=2),
        ),
    )
    try:
        items = list(_search_client().search(request).results)
        if not items:  # serving replicas are eventually consistent after a re-import; retry once
            items = list(_search_client().search(request).results)
    except Exception as e:
        return {"status": "error", "message": f"Policy search failed: {e}"}

    results = []
    for item in items:
        doc = item.document
        meta = dict(doc.struct_data)
        derived = dict(doc.derived_struct_data)
        excerpts = [
            {"page": s.get("pageNumber"), "text": s.get("content")}
            for s in derived.get("extractive_segments", [])
        ]
        results.append({**_edition(meta), "source_uri": derived.get("link"), "excerpts": excerpts})
        if len(results) == 4:
            break

    if not results:
        return {"status": "no_results", "edition_selection": selection,
                "message": "Nothing relevant found in the governing edition. Say you don't know."}

    other_years = sorted({
        e["plan_year"] for e in catalog
        if any(e["policy_type"] == r["policy_type"] for r in results)
        and e["plan_year"] not in {r["plan_year"] for r in results}
    })
    return {
        "status": "ok",
        "edition_selection": selection,
        "results": results,
        "other_plan_years_in_corpus": other_years,
    }
