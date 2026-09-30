import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey
)
from sqlalchemy.orm import relationship
from app.db import Base


def generate_uuid(prefix: str = "") -> str:
    val = str(uuid.uuid4())
    return f"{prefix}_{val}" if prefix else val


class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(String, primary_key=True, default=lambda: generate_uuid("wf"))
    name = Column(String, nullable=False)
    prompt = Column(Text, nullable=False)
    plan_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    schedule_cron = Column(String, nullable=True)
    parent_workflow_id = Column(String, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)

    runs = relationship("Run", back_populates="workflow", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"

    id = Column(String, primary_key=True, default=lambda: generate_uuid("run"))
    workflow_id = Column(String, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="queued")  # queued|running|completed|failed|cancelled
    plan_snapshot = Column(JSON, nullable=False)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)
    stats_json = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    workflow = relationship("Workflow", back_populates="runs")
    events = relationship("RunEvent", back_populates="run", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="run", cascade="all, delete-orphan")
    records = relationship("Record", back_populates="run", cascade="all, delete-orphan")
    diffs = relationship("RunDiff", foreign_keys="[RunDiff.run_id]", back_populates="run", cascade="all, delete-orphan")


class RunEvent(Base):
    __tablename__ = "run_events"

    id = Column(Integer, primary_key=True, autoincrement=True)  # SSE event id
    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    ts = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    stage = Column(String, nullable=False)  # PLAN|POLICY|DISCOVER|FETCH|EXTRACT|VERIFY|DEDUPE|FINALIZE
    level = Column(String, nullable=False, default="info")  # info|warn|error|success
    message = Column(Text, nullable=False)
    data_json = Column(JSON, nullable=True)

    run = relationship("Run", back_populates="events")


class Source(Base):
    __tablename__ = "sources"

    id = Column(String, primary_key=True, default=lambda: generate_uuid("src"))
    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False)
    domain = Column(String, nullable=False)
    title = Column(String, nullable=True)
    policy_status = Column(String, nullable=False, default="allowed")  # allowed|blocked|api_used|skipped
    policy_reason = Column(Text, nullable=True)
    fetch_status = Column(String, nullable=False, default="pending")  # pending|success|failed|skipped
    http_status = Column(Integer, nullable=True)
    content_hash = Column(String, nullable=True)
    text = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=True)
    retries = Column(Integer, default=0)

    run = relationship("Run", back_populates="sources")
    evidence_items = relationship("Evidence", back_populates="source", cascade="all, delete-orphan")


class Record(Base):
    __tablename__ = "records"

    id = Column(String, primary_key=True, default=lambda: generate_uuid("rec"))
    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    values_json = Column(JSON, nullable=False)  # {col_name: value}
    status = Column(String, nullable=False, default="verified")  # verified|needs_review|rejected
    row_confidence = Column(Float, nullable=False, default=0.0)
    sources_count = Column(Integer, nullable=False, default=1)
    dedupe_key = Column(String, nullable=True, index=True)
    conflict_json = Column(JSON, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    run = relationship("Run", back_populates="records")
    evidence_items = relationship("Evidence", back_populates="record", cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(String, primary_key=True, default=lambda: generate_uuid("evi"))
    record_id = Column(String, ForeignKey("records.id", ondelete="CASCADE"), nullable=False)
    column = Column(String, nullable=False)
    value = Column(Text, nullable=True)
    source_id = Column(String, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    quote = Column(Text, nullable=False)
    quote_verified = Column(Boolean, nullable=False, default=False)
    confidence = Column(Float, nullable=False, default=0.0)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)

    record = relationship("Record", back_populates="evidence_items")
    source = relationship("Source", back_populates="evidence_items")


class RunDiff(Base):
    __tablename__ = "run_diffs"

    id = Column(String, primary_key=True, default=lambda: generate_uuid("diff"))
    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    previous_run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    added_json = Column(JSON, nullable=False, default=list)
    removed_json = Column(JSON, nullable=False, default=list)
    changed_json = Column(JSON, nullable=False, default=list)

    run = relationship("Run", foreign_keys=[run_id], back_populates="diffs")
