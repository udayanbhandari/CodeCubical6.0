from datetime import datetime
from typing import List, Optional, Any, Literal, Dict
from pydantic import BaseModel, Field, ConfigDict

# --- Plan Schema ---

class ColumnSchema(BaseModel):
    name: str
    type: Literal["string", "number", "date", "url", "boolean", "enum"]
    description: str
    required: bool = False
    enum_values: Optional[List[str]] = None


class SourceStrategy(BaseModel):
    preferred_source_types: List[str] = Field(default_factory=list)
    seed_urls: List[str] = Field(default_factory=list)
    allowed_domains: List[str] = Field(default_factory=list)
    blocked_domains: List[str] = Field(
        default_factory=lambda: ["linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com"]
    )
    max_pages: int = 25


class ValidationRule(BaseModel):
    column: str
    rule: Literal["regex", "range", "enum", "non_empty", "url_reachable", "date_not_past"]
    param: Optional[str] = None


class Plan(BaseModel):
    title: str
    objective: str
    entity_type: str
    columns: List[ColumnSchema]
    unique_key_columns: List[str]
    search_queries: List[str]
    source_strategy: SourceStrategy
    extraction_instructions: str
    validation_rules: List[ValidationRule] = Field(default_factory=list)
    min_sources_for_verified: int = 1


# --- API Request / Response Schemas ---

class PlanCreateRequest(BaseModel):
    prompt: str
    feedback: Optional[str] = None
    previous_plan: Optional[Plan] = None


class WorkflowCreate(BaseModel):
    name: str
    prompt: str
    plan: Plan
    schedule_cron: Optional[str] = None
    parent_workflow_id: Optional[str] = None


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    prompt: str
    plan_json: Plan
    created_at: datetime
    updated_at: datetime
    schedule_cron: Optional[str] = None
    parent_workflow_id: Optional[str] = None


class RunCreate(BaseModel):
    workflow_id: Optional[str] = None
    plan: Optional[Plan] = None
    name: Optional[str] = None


class RunEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    ts: datetime
    stage: str
    level: str
    message: str
    data_json: Optional[Dict[str, Any]] = None


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    url: str
    domain: str
    title: Optional[str] = None
    policy_status: str
    policy_reason: Optional[str] = None
    fetch_status: str
    http_status: Optional[int] = None
    content_hash: Optional[str] = None
    fetched_at: Optional[datetime] = None
    retries: int = 0


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    record_id: str
    column: str
    value: Optional[str] = None
    source_id: Optional[str] = None
    quote: str
    quote_verified: bool
    confidence: float
    char_start: Optional[int] = None
    char_end: Optional[int] = None


class RecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    values_json: Dict[str, Any]
    status: str
    row_confidence: float
    sources_count: int
    dedupe_key: Optional[str] = None
    conflict_json: Optional[Dict[str, Any]] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    evidence_items: List[EvidenceResponse] = Field(default_factory=list)


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: Optional[str] = None
    status: str
    plan_snapshot: Plan
    started_at: datetime
    finished_at: Optional[datetime] = None
    stats_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class RunDiffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    previous_run_id: str
    added_json: List[Any]
    removed_json: List[Any]
    changed_json: List[Any]
