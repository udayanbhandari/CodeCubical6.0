import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models import RunEvent

logger = logging.getLogger("proofdata.events")

# In-memory per-run asyncio event queues
_RUN_QUEUES: Dict[str, set[asyncio.Queue]] = {}


class RunContext:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.cancelled = False

    async def emit(self, stage: str, level: str, message: str, data: Optional[Dict[str, Any]] = None):
        """Persist event to database and broadcast to active SSE subscribers."""
        ts = datetime.now(timezone.utc)
        db: Session = SessionLocal()
        try:
            db_event = RunEvent(
                run_id=self.run_id,
                ts=ts,
                stage=stage.upper(),
                level=level.lower(),
                message=message,
                data_json=data
            )
            db.add(db_event)
            db.commit()
            db.refresh(db_event)
            event_id = db_event.id
        except Exception as e:
            logger.error(f"Failed to persist RunEvent for run {self.run_id}: {e}")
            event_id = 0
        finally:
            db.close()

        payload = {
            "id": event_id,
            "run_id": self.run_id,
            "ts": ts.isoformat(),
            "stage": stage.upper(),
            "level": level.lower(),
            "message": message,
            "data": data
        }

        # Broadcast to active listening queues
        if self.run_id in _RUN_QUEUES:
            for q in list(_RUN_QUEUES[self.run_id]):
                try:
                    q.put_nowait(payload)
                except Exception:
                    pass


def subscribe_run_events(run_id: str) -> asyncio.Queue:
    """Create a subscription queue for live SSE streaming."""
    q = asyncio.Queue()
    if run_id not in _RUN_QUEUES:
        _RUN_QUEUES[run_id] = set()
    _RUN_QUEUES[run_id].add(q)
    return q


def unsubscribe_run_events(run_id: str, q: asyncio.Queue):
    """Remove a subscription queue."""
    if run_id in _RUN_QUEUES and q in _RUN_QUEUES[run_id]:
        _RUN_QUEUES[run_id].remove(q)
        if not _RUN_QUEUES[run_id]:
            del _RUN_QUEUES[run_id]
