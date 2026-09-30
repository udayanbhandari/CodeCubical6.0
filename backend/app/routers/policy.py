from fastapi import APIRouter
from app.pipeline.policy import get_policy_rules_description

router = APIRouter(prefix="/api/policy", tags=["Policy"])


@router.get("")
async def get_policy_rules():
    """Return plain-English summary of active system policy rules for UI."""
    return get_policy_rules_description()
