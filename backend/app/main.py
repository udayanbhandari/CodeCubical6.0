import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import settings
from app.db import init_db
from app.pipeline.orchestrator import clean_orphaned_runs
from app.routers import plan, policy, runs, workflows

logger = logging.getLogger("proofdata.main")
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    logger.info("Initializing ProofData Database tables...")
    init_db()

    logger.info("Cleaning orphaned running tasks...")
    clean_orphaned_runs()

    # Start APScheduler for cron workflows
    try:
        scheduler.start()
        logger.info("APScheduler initialized for recurring workflow schedules.")
    except Exception as e:
        logger.warning(f"Scheduler start warning: {e}")

    yield

    # Shutdown tasks
    try:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")
    except Exception:
        pass


app = FastAPI(
    title="ProofData API",
    description="AI-powered data intelligence platform engine with strict receipt verification.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router Registrations
app.include_router(plan.router)
app.include_router(policy.router)
app.include_router(runs.router)
app.include_router(workflows.router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "ok",
        "app": "ProofData API",
        "version": "1.0.0"
    }
