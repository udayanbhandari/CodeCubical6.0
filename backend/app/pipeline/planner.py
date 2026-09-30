import logging
from typing import Optional
from app.schemas import Plan, ColumnSchema, SourceStrategy, ValidationRule
from app.llm import generate_json
from app.config import settings

logger = logging.getLogger("proofdata.planner")

PLANNER_SYSTEM_PROMPT = """You are ProofData Planner, an expert AI data engineer. Your job is to take a user's data extraction request and design a precise, minimal, receipt-verifiable data collection plan.

RULES:
1. Entity Type: Identify the core entity being extracted (e.g., "Company", "Grant", "Conference", "Executive", "Product").
2. Columns: Infer 4 to 10 minimal, highly useful columns. Keep column names snake_case.
   Supported column types: string, number, date, url, boolean, enum.
   If type is enum, specify enum_values.
3. Unique Key Columns: Select 1-2 columns that uniquely identify each record (e.g., ["name"] or ["company_name", "batch"]).
4. Search Queries: Generate 3 to 8 diverse search queries covering official sites, directories, registries, and news.
5. Source Strategy:
   - Always include "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com" in blocked_domains unless the user explicitly requests them.
   - Do NOT invent seed URLs. Only include seed_urls if explicitly given in the prompt.
   - Set max_pages between 10 and 25.
6. Validation Rules:
   - For 'url' columns, add a rule with rule='url_reachable'.
   - For date or deadline columns, add rule='date_not_past' or 'range' if appropriate.
7. Extraction Instructions: Clear guidance on what data to look for and how to handle edge cases.
8. Ambiguity: If the prompt is ambiguous, make sensible decisions and state your assumptions clearly in the 'objective' field.
9. Output ONLY a valid JSON matching the Plan schema. No code block markers or conversational preamble.
"""


def _generate_heuristic_plan(prompt: str) -> Plan:
    """Fallback plan generator when LLM API key is not configured."""
    p_lower = prompt.lower()

    if "company" in p_lower or "startup" in p_lower or "yc" in p_lower:
        entity_type = "Company"
        title = "Company Data Extraction Plan"
        cols = [
            ColumnSchema(name="name", type="string", description="Official name of the company", required=True),
            ColumnSchema(name="website", type="url", description="Company official website URL", required=True),
            ColumnSchema(name="description", type="string", description="One-line summary of what the company does"),
            ColumnSchema(name="founded_year", type="number", description="Year the company was established"),
            ColumnSchema(name="category", type="string", description="Industry or sector category"),
            ColumnSchema(name="hq_location", type="string", description="Headquarters city or country"),
        ]
        unique_keys = ["name"]
        queries = [
            f"{prompt} official directory",
            f"{prompt} list profile location founded",
            f"{prompt} top companies website category"
        ]
    elif "grant" in p_lower or "funding" in p_lower:
        entity_type = "Grant"
        title = "Grant & Funding Opportunity Plan"
        cols = [
            ColumnSchema(name="grant_title", type="string", description="Name of the grant or funding program", required=True),
            ColumnSchema(name="organization", type="string", description="Grantor or issuing foundation", required=True),
            ColumnSchema(name="amount", type="string", description="Maximum or average grant funding amount"),
            ColumnSchema(name="deadline", type="date", description="Application closing date"),
            ColumnSchema(name="website", type="url", description="Official application link"),
        ]
        unique_keys = ["grant_title", "organization"]
        queries = [
            f"{prompt} open application deadline",
            f"{prompt} official portal requirements eligibility"
        ]
    else:
        entity_type = "Item"
        title = f"Data Plan for: {prompt[:40]}"
        cols = [
            ColumnSchema(name="name", type="string", description="Primary name or title", required=True),
            ColumnSchema(name="details", type="string", description="Supporting details and summary"),
            ColumnSchema(name="source_link", type="url", description="Relevant URL"),
        ]
        unique_keys = ["name"]
        queries = [
            f"{prompt} list details",
            f"{prompt} official directory source"
        ]

    val_rules = []
    for c in cols:
        if c.type == "url":
            val_rules.append(ValidationRule(column=c.name, rule="url_reachable"))
        elif c.name in ("deadline", "event_date"):
            val_rules.append(ValidationRule(column=c.name, rule="date_not_past"))

    return Plan(
        title=title,
        objective=f"Extracted dataset based on prompt: '{prompt}'. Assumed primary focus on {entity_type}s.",
        entity_type=entity_type,
        columns=cols,
        unique_key_columns=unique_keys,
        search_queries=queries,
        source_strategy=SourceStrategy(
            preferred_source_types=["official", "directory"],
            seed_urls=[],
            allowed_domains=[],
            blocked_domains=["linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com"],
            max_pages=min(25, settings.MAX_PAGES_PER_RUN)
        ),
        extraction_instructions="Extract explicitly stated facts only. Never infer or invent data.",
        validation_rules=val_rules,
        min_sources_for_verified=1
    )


async def build_plan(prompt: str, feedback: Optional[str] = None, previous: Optional[Plan] = None) -> Plan:
    """Build or update a data extraction plan based on prompt and optional feedback."""
    user_prompt = f"Data Need Request: {prompt}"
    if previous and feedback:
        user_prompt += (
            f"\n\n[PREVIOUS PLAN TO REVISE]\n{previous.model_dump_json(indent=2)}\n\n"
            f"[USER FEEDBACK / REVISION INSTRUCTIONS]\n{feedback}\n"
            f"Revise the plan minimally based on the user's feedback."
        )

    try:
        if not settings.LLM_API_KEY or settings.LLM_API_KEY.startswith("your_"):
            logger.info("Using heuristic planner fallback (no LLM_API_KEY configured).")
            return _generate_heuristic_plan(prompt)

        return await generate_json(
            system=PLANNER_SYSTEM_PROMPT,
            user=user_prompt,
            model_cls=Plan,
            max_retries=2
        )
    except Exception as e:
        logger.warning(f"Planner LLM call failed or fallback triggered ({e}). Using heuristic plan generator.")
        return _generate_heuristic_plan(prompt)
