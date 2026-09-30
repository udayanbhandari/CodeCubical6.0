from fastapi import APIRouter, HTTPException
from app.schemas import PlanCreateRequest, Plan
from app.pipeline.planner import build_plan

router = APIRouter(prefix="/api/plan", tags=["Plan"])


@router.post("", response_model=Plan)
async def create_plan(request: PlanCreateRequest):
    """Generate or revise a data collection plan from a prompt and optional feedback."""
    if not request.prompt or not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt must not be empty.")

    try:
        plan = await build_plan(
            prompt=request.prompt,
            feedback=request.feedback,
            previous=request.previous_plan
        )
        return plan
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate plan: {str(e)}")
