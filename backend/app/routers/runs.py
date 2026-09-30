import io
import csv
import json
import asyncio
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Run, Source, Record, Evidence, RunEvent, RunDiff, Workflow
from app.schemas import (
    RunCreate, RunResponse, RunEventResponse, SourceResponse,
    RecordResponse, EvidenceResponse, RunDiffResponse
)
from app.events import subscribe_run_events, unsubscribe_run_events
from app.pipeline.orchestrator import execute_run, cancel_run_execution
from app.pipeline.planner import build_plan

router = APIRouter(prefix="/api/runs", tags=["Runs"])


@router.post("", response_model=RunResponse)
async def create_run(payload: RunCreate, db: Session = Depends(get_db)):
    """Create a new run and initiate background pipeline execution."""
    plan = payload.plan
    if not plan and payload.workflow_id:
        wf = db.query(Workflow).filter(Workflow.id == payload.workflow_id).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        plan = wf.plan_json

    if not plan and payload.name:
        plan = await build_plan(payload.name)

    if not plan:
        raise HTTPException(status_code=400, detail="Either plan, workflow_id, or name prompt must be provided.")

    plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else plan

    run_db = Run(
        workflow_id=payload.workflow_id,
        status="queued",
        plan_snapshot=plan_dict
    )
    db.add(run_db)
    db.commit()
    db.refresh(run_db)

    # Launch background async task
    asyncio.create_task(execute_run(run_db.id))

    return run_db


@router.get("", response_model=List[RunResponse])
async def list_runs(db: Session = Depends(get_db)):
    """List all data collection runs ordered by creation timestamp."""
    runs = db.query(Run).order_by(Run.started_at.desc()).all()
    return runs


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, db: Session = Depends(get_db)):
    """Get single run by ID."""
    run_db = db.query(Run).filter(Run.id == run_id).first()
    if not run_db:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run_db


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, db: Session = Depends(get_db)):
    """Cancel an ongoing run."""
    run_db = db.query(Run).filter(Run.id == run_id).first()
    if not run_db:
        raise HTTPException(status_code=404, detail="Run not found.")
    cancel_run_execution(run_id)
    return {"status": "cancelling", "run_id": run_id}


@router.get("/{run_id}/events", response_model=List[RunEventResponse])
async def list_run_events(run_id: str, db: Session = Depends(get_db)):
    """List all events for a run."""
    events = db.query(RunEvent).filter(RunEvent.run_id == run_id).order_by(RunEvent.id.asc()).all()
    return events


@router.get("/{run_id}/events/stream")
async def stream_run_events(run_id: str, db: Session = Depends(get_db)):
    """SSE live streaming endpoint for run timeline events."""
    q = subscribe_run_events(run_id)

    async def event_generator():
        try:
            # Replay existing events first
            existing = db.query(RunEvent).filter(RunEvent.run_id == run_id).order_by(RunEvent.id.asc()).all()
            for ev in existing:
                yield {
                    "id": str(ev.id),
                    "event": "message",
                    "data": json.dumps({
                        "id": ev.id,
                        "run_id": ev.run_id,
                        "ts": ev.ts.isoformat(),
                        "stage": ev.stage,
                        "level": ev.level,
                        "message": ev.message,
                        "data": ev.data_json
                    })
                }

            # Stream live queue events
            while True:
                data = await q.get()
                yield {
                    "id": str(data.get("id", "")),
                    "event": "message",
                    "data": json.dumps(data)
                }
                if data.get("stage") == "FINALIZE":
                    yield {"event": "done", "data": "Run completed"}
                    break
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe_run_events(run_id, q)

    return EventSourceResponse(event_generator())


@router.get("/{run_id}/sources", response_model=List[SourceResponse])
async def list_run_sources(run_id: str, db: Session = Depends(get_db)):
    """List all sources for a run."""
    sources = db.query(Source).filter(Source.run_id == run_id).all()
    return sources


@router.get("/{run_id}/records", response_model=List[RecordResponse])
async def get_run_records(
    run_id: str,
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    min_conf: Optional[float] = Query(None),
    db: Session = Depends(get_db)
):
    """Query records for a run with status, text search, and confidence filtering."""
    query = db.query(Record).filter(Record.run_id == run_id)
    if status:
        query = query.filter(Record.status == status)
    if min_conf is not None:
        query = query.filter(Record.row_confidence >= min_conf)

    records = query.all()
    if q:
        q_lower = q.lower()
        records = [
            r for r in records
            if any(q_lower in str(v).lower() for v in r.values_json.values() if v is not None)
        ]
    return records


@router.get("/records/{record_id}/evidence", response_model=List[EvidenceResponse])
async def get_record_evidence(record_id: str, db: Session = Depends(get_db)):
    """Get all evidence items supporting a record."""
    evidence = db.query(Evidence).filter(Evidence.record_id == record_id).all()
    return evidence


@router.get("/sources/{source_id}/text")
async def get_source_text(source_id: str, db: Session = Depends(get_db)):
    """Get extracted text for a specific source."""
    src = db.query(Source).filter(Source.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found.")
    return {"id": src.id, "url": src.url, "text": src.text}


@router.get("/{run_id}/diff", response_model=Optional[RunDiffResponse])
async def get_run_diff(run_id: str, db: Session = Depends(get_db)):
    """Get run diff vs previous run."""
    diff = db.query(RunDiff).filter(RunDiff.run_id == run_id).first()
    return diff


@router.get("/{run_id}/export")
async def export_run_data(
    run_id: str,
    format: str = Query("csv", pattern="^(csv|json)$"),
    provenance: bool = Query(True),
    db: Session = Depends(get_db)
):
    """Export extracted records to CSV or JSON with optional receipt provenance columns."""
    records = db.query(Record).filter(Record.run_id == run_id).all()
    if not records:
        raise HTTPException(status_code=404, detail="No records found for export.")

    if format == "json":
        export_data = []
        for r in records:
            item = {
                "id": r.id,
                "status": r.status,
                "confidence": r.row_confidence,
                "values": r.values_json
            }
            if provenance:
                item["evidence"] = [
                    {
                        "column": e.column,
                        "quote": e.quote,
                        "verified": e.quote_verified,
                        "confidence": e.confidence
                    } for e in r.evidence_items
                ]
            export_data.append(item)
        return Response(content=json.dumps(export_data, indent=2), media_type="application/json")

    # CSV Export with UTF-8 BOM (\ufeff)
    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM for Excel compatibility

    sample_keys = list(records[0].values_json.keys()) if records else []
    fieldnames = list(sample_keys) + ["status", "row_confidence"]
    if provenance:
        fieldnames.extend(["source_url", "supporting_quote"])

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for r in records:
        row = {**r.values_json, "status": r.status, "row_confidence": r.row_confidence}
        if provenance and r.evidence_items:
            first_ev = r.evidence_items[0]
            src = db.query(Source).filter(Source.id == first_ev.source_id).first() if first_ev.source_id else None
            row["source_url"] = src.url if src else ""
            row["supporting_quote"] = first_ev.quote
        elif provenance:
            row["source_url"] = ""
            row["supporting_quote"] = ""
        writer.writerow(row)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=proofdata_run_{run_id}.csv"}
    )
