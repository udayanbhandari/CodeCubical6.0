import asyncio
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Workflow, Run, Record
from app.schemas import WorkflowCreate, WorkflowResponse, RunResponse
from app.pipeline.orchestrator import execute_run

router = APIRouter(prefix="/api/workflows", tags=["Workflows"])


@router.get("", response_model=List[WorkflowResponse])
async def list_workflows(db: Session = Depends(get_db)):
    """List all saved data workflows."""
    return db.query(Workflow).order_by(Workflow.updated_at.desc()).all()


@router.post("", response_model=WorkflowResponse)
async def create_workflow(payload: WorkflowCreate, db: Session = Depends(get_db)):
    """Create a new workflow."""
    plan_dict = payload.plan.model_dump() if hasattr(payload.plan, "model_dump") else payload.plan
    wf = Workflow(
        name=payload.name,
        prompt=payload.prompt,
        plan_json=plan_dict,
        schedule_cron=payload.schedule_cron,
        parent_workflow_id=payload.parent_workflow_id
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


@router.get("/{wf_id}", response_model=WorkflowResponse)
async def get_workflow(wf_id: str, db: Session = Depends(get_db)):
    """Get workflow by ID."""
    wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return wf


@router.put("/{wf_id}", response_model=WorkflowResponse)
async def update_workflow(wf_id: str, payload: WorkflowCreate, db: Session = Depends(get_db)):
    """Update workflow plan or metadata."""
    wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    plan_dict = payload.plan.model_dump() if hasattr(payload.plan, "model_dump") else payload.plan
    wf.name = payload.name
    wf.prompt = payload.prompt
    wf.plan_json = plan_dict
    wf.schedule_cron = payload.schedule_cron
    db.commit()
    db.refresh(wf)
    return wf


@router.delete("/{wf_id}")
async def delete_workflow(wf_id: str, db: Session = Depends(get_db)):
    """Delete workflow and associated runs."""
    wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    db.delete(wf)
    db.commit()
    return {"status": "deleted", "id": wf_id}


@router.post("/{wf_id}/clone", response_model=WorkflowResponse)
async def clone_workflow(wf_id: str, db: Session = Depends(get_db)):
    """Clone an existing workflow."""
    parent = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent workflow not found.")

    cloned = Workflow(
        name=f"{parent.name} (Copy)",
        prompt=parent.prompt,
        plan_json=parent.plan_json,
        parent_workflow_id=parent.id
    )
    db.add(cloned)
    db.commit()
    db.refresh(cloned)
    return cloned


@router.post("/{wf_id}/run", response_model=RunResponse)
async def run_workflow(wf_id: str, db: Session = Depends(get_db)):
    """Trigger a new run for a specific workflow."""
    wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    run_db = Run(
        workflow_id=wf.id,
        status="queued",
        plan_snapshot=wf.plan_json
    )
    db.add(run_db)
    db.commit()
    db.refresh(run_db)

    asyncio.create_task(execute_run(run_db.id))
    return run_db


@router.put("/{wf_id}/schedule", response_model=WorkflowResponse)
async def set_workflow_schedule(wf_id: str, cron: Optional[str] = Body(None, embed=True), db: Session = Depends(get_db)):
    """Update recurring cron schedule for workflow."""
    wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    wf.schedule_cron = cron
    db.commit()
    db.refresh(wf)
    return wf


@router.get("/{wf_id}/trend")
async def get_workflow_trend(wf_id: str, db: Session = Depends(get_db)):
    """Return historical row counts and avg confidence per run for UI trend charts."""
    runs = db.query(Run).filter(Run.workflow_id == wf_id, Run.status == "completed").order_by(Run.finished_at.asc()).all()
    trend_data = []
    for r in runs:
        stats = r.stats_json or {}
        trend_data.append({
            "run_id": r.id,
            "timestamp": r.finished_at.isoformat() if r.finished_at else r.started_at.isoformat(),
            "record_count": stats.get("verified_count", 0),
            "avg_confidence": stats.get("avg_confidence", 0.0)
        })
    return {"workflow_id": wf_id, "trend": trend_data}
