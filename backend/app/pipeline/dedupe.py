import re
import logging
from typing import List, Dict, Any, Tuple
from rapidfuzz import fuzz
from app.schemas import Plan

logger = logging.getLogger("proofdata.dedupe")

_SUFFIX_REGEX = re.compile(r"\b(pvt|ltd|inc|llp|corp|co|limited|incorporated|corporation|gmbh|plc)\b", re.IGNORECASE)


def generate_dedupe_key(values: Dict[str, Any], unique_key_columns: List[str]) -> str:
    """Generate normalized dedupe key from unique_key_columns."""
    key_parts = []
    for col in unique_key_columns:
        val = values.get(col)
        if val is not None:
            v_str = str(val).lower()
            v_clean = re.sub(r"[^\w\s]", "", v_str)
            v_clean = _SUFFIX_REGEX.sub("", v_clean)
            key_parts.append(re.sub(r"\s+", " ", v_clean).strip())

    return "::".join(key_parts) if key_parts else "unkeyed"


def merge_record_cluster(cluster: List[Dict[str, Any]], plan: Plan) -> Dict[str, Any]:
    """Merge a cluster of records into a single deduplicated Record."""
    if len(cluster) == 1:
        rec = cluster[0]
        sources = {ev["source_id"] for ev in rec.get("evidence_items", []) if ev.get("source_id")}
        sources_count = max(len(sources), 1)

        status = rec["status"]
        if plan.min_sources_for_verified > sources_count and status == "verified":
            status = "needs_review"

        return {
            **rec,
            "sources_count": sources_count,
            "status": status
        }

    merged_values: Dict[str, Any] = {}
    merged_evidence: List[Dict[str, Any]] = []
    conflict_json: Dict[str, List[Dict[str, Any]]] = {}
    all_source_ids = set()
    seen_evidence_keys = set()

    # Collect all unique evidence items
    for rec in cluster:
        for ev in rec.get("evidence_items", []):
            ev_key = (ev.get("column"), ev.get("source_id"), ev.get("quote"))
            if ev_key not in seen_evidence_keys:
                seen_evidence_keys.add(ev_key)
                merged_evidence.append(ev)
            if ev.get("source_id"):
                all_source_ids.add(ev["source_id"])

    # Column value merging & conflict detection
    for col_schema in plan.columns:
        col_name = col_schema.name
        col_candidates: List[Tuple[Any, float, Dict[str, Any]]] = []

        for rec in cluster:
            val = rec["values_json"].get(col_name)
            for ev in rec.get("evidence_items", []):
                if ev["column"] == col_name and ev.get("quote_verified") and val is not None:
                    col_candidates.append((val, ev["confidence"], ev))

        if not col_candidates:
            merged_values[col_name] = None
        else:
            col_candidates.sort(key=lambda x: x[1], reverse=True)
            best_val, best_conf, best_ev = col_candidates[0]
            merged_values[col_name] = best_val

            alt_values = []
            for val, conf, ev in col_candidates[1:]:
                if str(val).strip().lower() != str(best_val).strip().lower():
                    alt_values.append({
                        "value": val,
                        "confidence": conf,
                        "source_id": ev.get("source_id")
                    })

            if alt_values:
                conflict_json[col_name] = alt_values

    confidences = [ev["confidence"] for ev in merged_evidence if ev.get("quote_verified")]
    avg_conf = round(sum(confidences) / max(len(confidences), 1), 3) if confidences else 0.0
    sources_count = max(len(all_source_ids), 1)

    if avg_conf >= 0.75:
        status = "verified"
    elif avg_conf >= 0.4:
        status = "needs_review"
    else:
        status = "rejected"

    if plan.min_sources_for_verified > sources_count and status == "verified":
        status = "needs_review"

    dedupe_key = generate_dedupe_key(merged_values, plan.unique_key_columns)

    return {
        "values_json": merged_values,
        "status": status,
        "row_confidence": avg_conf,
        "sources_count": sources_count,
        "dedupe_key": dedupe_key,
        "conflict_json": conflict_json if conflict_json else None,
        "rejection_reason": None if status != "rejected" else "Low merged confidence score.",
        "evidence_items": merged_evidence
    }


def dedupe_records(records: List[Dict[str, Any]], plan: Plan) -> List[Dict[str, Any]]:
    """Deduplicate records by key matching and fuzzy token_set_ratio >= 90 clustering."""
    if not records:
        return []

    for rec in records:
        rec["dedupe_key"] = generate_dedupe_key(rec["values_json"], plan.unique_key_columns)

    clusters: List[List[Dict[str, Any]]] = []
    used_indices = set()

    for i in range(len(records)):
        if i in used_indices:
            continue
        cluster = [records[i]]
        used_indices.add(i)
        key_i = records[i]["dedupe_key"]

        for j in range(i + 1, len(records)):
            if j in used_indices:
                continue
            key_j = records[j]["dedupe_key"]

            is_match = False
            if key_i != "unkeyed" and key_i == key_j:
                is_match = True
            elif key_i != "unkeyed" and key_j != "unkeyed":
                score = fuzz.token_set_ratio(key_i, key_j)
                if score >= 90:
                    is_match = True

            if is_match:
                cluster.append(records[j])
                used_indices.add(j)

        clusters.append(cluster)

    deduped_records = [merge_record_cluster(c, plan) for c in clusters]
    return deduped_records


def generate_quality_report(
    sources: List[Dict[str, Any]],
    raw_records_count: int,
    final_records: List[Dict[str, Any]],
    plan: Plan
) -> Dict[str, Any]:
    """Generate comprehensive quality and pipeline execution statistics."""
    pages_discovered = len(sources)
    pages_fetched = sum(1 for s in sources if s.get("fetch_status") == "success")
    pages_blocked = sum(1 for s in sources if s.get("policy_status") == "blocked")
    pages_failed = sum(1 for s in sources if s.get("fetch_status") == "failed")

    verified_count = sum(1 for r in final_records if r["status"] == "verified")
    needs_review_count = sum(1 for r in final_records if r["status"] == "needs_review")
    rejected_count = sum(1 for r in final_records if r["status"] == "rejected")
    duplicates_removed = max(0, raw_records_count - len(final_records))

    multi_source_verified = sum(
        1 for r in final_records if r["status"] == "verified" and r.get("sources_count", 1) > 1
    )

    confidences = [r.get("row_confidence", 0.0) for r in final_records]
    avg_confidence = round(sum(confidences) / max(len(confidences), 1), 3) if confidences else 0.0

    col_counts = {col.name: 0 for col in plan.columns}
    cells_rejected_unverified_quote = 0

    for r in final_records:
        vals = r.get("values_json", {})
        for col_name, val in vals.items():
            if val is not None:
                col_counts[col_name] = col_counts.get(col_name, 0) + 1

        for ev in r.get("evidence_items", []):
            if not ev.get("quote_verified"):
                cells_rejected_unverified_quote += 1

    total_recs = max(len(final_records), 1)
    per_column_fill_rate = {
        col: round((count / total_recs) * 100, 1)
        for col, count in col_counts.items()
    }

    return {
        "pages_discovered": pages_discovered,
        "pages_fetched": pages_fetched,
        "pages_blocked": pages_blocked,
        "pages_failed": pages_failed,
        "records_extracted": raw_records_count,
        "records_final": len(final_records),
        "duplicates_removed": duplicates_removed,
        "verified_count": verified_count,
        "needs_review_count": needs_review_count,
        "rejected_count": rejected_count,
        "multi_source_verified": multi_source_verified,
        "avg_confidence": avg_confidence,
        "per_column_fill_rate": per_column_fill_rate,
        "cells_rejected_unverified_quote": cells_rejected_unverified_quote
    }
