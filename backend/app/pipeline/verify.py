import re
import unicodedata
import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple
from rapidfuzz import fuzz
from app.schemas import Plan, ColumnSchema, ValidationRule

logger = logging.getLogger("proofdata.verify")


def normalize_text(text: str) -> str:
    """Normalize unicode, whitespace, and smart quotes for string comparison."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    text = text.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def find_quote_offsets(quote: str, raw_source_text: str) -> Tuple[bool, bool, Optional[int], Optional[int]]:
    """Locate verbatim or fuzzy quote in original source text.

    Returns: (quote_verified, is_exact, char_start, char_end)
    """
    if not quote or not quote.strip() or not raw_source_text:
        return False, False, None, None

    # 1. Exact Substring Search in Original Raw Text
    idx = raw_source_text.find(quote)
    if idx != -1:
        return True, True, idx, idx + len(quote)

    # 2. Normalized Text Substring Search
    norm_quote = normalize_text(quote)
    norm_source = normalize_text(raw_source_text)

    norm_idx = norm_source.find(norm_quote)
    if norm_idx != -1 and len(norm_quote) > 5:
        ratio = len(raw_source_text) / max(len(norm_source), 1)
        approx_start = int(norm_idx * ratio)
        approx_end = min(len(raw_source_text), int((norm_idx + len(norm_quote)) * ratio))
        return True, True, approx_start, approx_end

    # 3. RapidFuzz Fuzzy Match (partial_ratio >= 88 or token_set_ratio >= 90)
    p_score = fuzz.partial_ratio(norm_quote, norm_source)
    t_score = fuzz.token_set_ratio(norm_quote, norm_source)

    if (p_score >= 88 or t_score >= 90) and len(norm_quote) >= 8:
        window_size = len(quote)
        best_score = 0
        best_start = 0

        step = max(1, len(raw_source_text) // 200)
        for i in range(0, max(1, len(raw_source_text) - window_size), step):
            sub = raw_source_text[i: i + window_size]
            score = max(
                fuzz.ratio(norm_quote, normalize_text(sub)),
                fuzz.token_set_ratio(norm_quote, normalize_text(sub))
            )
            if score > best_score:
                best_score = score
                best_start = i

        return True, False, best_start, min(len(raw_source_text), best_start + window_size)

    # 4. Failed verification
    return False, False, None, None


def validate_cell_value(val: Any, col_schema: ColumnSchema, rules: List[ValidationRule]) -> Tuple[bool, Optional[str]]:
    """Check value type and plan validation rules."""
    if val is None:
        if col_schema.required:
            return False, f"Required column '{col_schema.name}' is null or missing."
        return True, None

    val_str = str(val).strip()

    # Enum Validation
    if col_schema.type == "enum" and col_schema.enum_values:
        valid_enums = [v.lower() for v in col_schema.enum_values]
        if val_str.lower() not in valid_enums:
            return False, f"Value '{val_str}' not in allowed enum values {col_schema.enum_values}"

    # Rule Validation
    for rule in rules:
        if rule.column == col_schema.name:
            if rule.rule == "non_empty" and not val_str:
                return False, f"Column '{rule.column}' fails non_empty rule."
            elif rule.rule == "regex" and rule.param:
                if not re.search(rule.param, val_str):
                    return False, f"Value '{val_str}' does not match regex rule: {rule.param}"
            elif rule.rule == "range" and rule.param:
                try:
                    num = float(val)
                    min_v, max_v = map(float, rule.param.split("-"))
                    if not (min_v <= num <= max_v):
                        return False, f"Value {num} outside allowed range {rule.param}"
                except Exception:
                    return False, f"Value '{val_str}' cannot be parsed for range validation."
            elif rule.rule == "date_not_past":
                try:
                    dt = datetime.strptime(val_str[:10], "%Y-%m-%d").date()
                    if dt < date.today():
                        return False, f"Date '{val_str}' is in the past."
                except Exception:
                    pass

    return True, None


def verify_record(
    raw_record: Dict[str, Any],
    source_text: str,
    source_id: str,
    plan: Plan
) -> Dict[str, Any]:
    """Verify raw extracted record against source text."""
    cells = raw_record.get("cells", {})
    verified_values: Dict[str, Any] = {}
    evidence_items: List[Dict[str, Any]] = []
    cell_confidences: List[float] = []
    rejection_reasons: List[str] = []

    for col_schema in plan.columns:
        col_name = col_schema.name
        cell_data = cells.get(col_name, {})
        val = cell_data.get("value")
        quote = cell_data.get("quote", "")
        llm_conf = float(cell_data.get("confidence", 0.8))

        if val is None or str(val).strip() == "":
            verified_values[col_name] = None
            if col_schema.required:
                rejection_reasons.append(f"Missing required column '{col_name}'.")
            continue

        # Core Invariant Verification: Check Quote in Source Text
        verified, is_exact, c_start, c_end = find_quote_offsets(quote, source_text)

        if not verified:
            logger.info(f"Unverified quote for column '{col_name}': '{quote}'. Rejecting cell value '{val}'.")
            verified_values[col_name] = None
            evidence_items.append({
                "column": col_name,
                "value": str(val),
                "source_id": source_id,
                "quote": quote,
                "quote_verified": False,
                "confidence": 0.0,
                "char_start": None,
                "char_end": None
            })
            if col_schema.required:
                rejection_reasons.append(f"Required column '{col_name}' rejected due to unverified quote.")
            continue

        # Validate Cell Type and Plan Rules
        rules_pass, rule_err = validate_cell_value(val, col_schema, plan.validation_rules)
        if not rules_pass:
            rejection_reasons.append(rule_err or f"Validation failed for column '{col_name}'.")

        match_multiplier = 1.0 if is_exact else 0.8
        rule_multiplier = 1.0 if rules_pass else 0.5
        final_cell_conf = llm_conf * match_multiplier * rule_multiplier

        verified_values[col_name] = val
        cell_confidences.append(final_cell_conf)

        evidence_items.append({
            "column": col_name,
            "value": str(val),
            "source_id": source_id,
            "quote": quote,
            "quote_verified": True,
            "confidence": round(final_cell_conf, 3),
            "char_start": c_start,
            "char_end": c_end
        })

    row_confidence = round(sum(cell_confidences) / max(len(cell_confidences), 1), 3) if cell_confidences else 0.0

    if rejection_reasons:
        status = "rejected"
        row_rejection_reason = " | ".join(rejection_reasons)
    elif row_confidence >= 0.75:
        status = "verified"
        row_rejection_reason = None
    elif row_confidence >= 0.4:
        status = "needs_review"
        row_rejection_reason = "Low cell confidence requires manual review."
    else:
        status = "rejected"
        row_rejection_reason = f"Row confidence {row_confidence} below minimum threshold 0.40."

    return {
        "values_json": verified_values,
        "status": status,
        "row_confidence": row_confidence,
        "sources_count": 1,
        "rejection_reason": row_rejection_reason,
        "evidence_items": evidence_items
    }
