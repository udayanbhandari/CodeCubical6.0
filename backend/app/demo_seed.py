import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
from datetime import datetime, timezone
from app.db import SessionLocal, init_db
from app.models import Workflow, Run, Source, Record, Evidence, RunEvent


def seed_demo_data():
    """Seed DB with 2 completed runs and full evidence receipts for offline demo UI."""
    init_db()
    db = SessionLocal()

    try:
        if db.query(Workflow).first():
            print("Database already contains workflows/runs. Skipping demo seed.")
            return

        print("Seeding database with demo workflows and runs...")

        plan1 = {
            "title": "YC W24 Startups Directory",
            "objective": "Extract active Y Combinator W24 companies with site, funding, and founders.",
            "entity_type": "Company",
            "columns": [
                {"name": "company_name", "type": "string", "description": "Company name", "required": True},
                {"name": "website", "type": "url", "description": "Official URL", "required": True},
                {"name": "batch", "type": "string", "description": "YC Batch e.g. W24"},
                {"name": "category", "type": "string", "description": "Industry domain"},
                {"name": "location", "type": "string", "description": "HQ location"}
            ],
            "unique_key_columns": ["company_name"],
            "search_queries": ["Y Combinator W24 companies directory", "YC W24 batch companies list"],
            "source_strategy": {
                "preferred_source_types": ["official", "directory"],
                "seed_urls": ["https://www.ycombinator.com/companies"],
                "allowed_domains": ["ycombinator.com"],
                "blocked_domains": ["linkedin.com", "facebook.com", "instagram.com", "x.com"],
                "max_pages": 15
            },
            "extraction_instructions": "Extract exact startup facts only.",
            "validation_rules": [{"column": "website", "rule": "url_reachable"}],
            "min_sources_for_verified": 1
        }

        wf1 = Workflow(
            id="wf_demo_yc",
            name="YC W24 Companies Tracker",
            prompt="Extract top Y Combinator W24 batch startups with official website and details",
            plan_json=plan1
        )
        db.add(wf1)
        db.flush()

        run1 = Run(
            id="run_demo_01",
            workflow_id=wf1.id,
            status="completed",
            plan_snapshot=plan1,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            stats_json={
                "pages_discovered": 4,
                "pages_fetched": 3,
                "pages_blocked": 1,
                "pages_failed": 0,
                "records_extracted": 3,
                "records_final": 3,
                "duplicates_removed": 0,
                "verified_count": 3,
                "needs_review_count": 0,
                "rejected_count": 0,
                "multi_source_verified": 2,
                "avg_confidence": 0.96,
                "per_column_fill_rate": {"company_name": 100.0, "website": 100.0, "batch": 100.0, "category": 100.0, "location": 100.0},
                "cells_rejected_unverified_quote": 0
            }
        )
        db.add(run1)
        db.flush()

        src1 = Source(
            id="src_yc_01",
            run_id=run1.id,
            url="https://www.ycombinator.com/companies",
            domain="ycombinator.com",
            title="Y Combinator Companies Directory",
            policy_status="allowed",
            fetch_status="success",
            http_status=200,
            text="Y Combinator W24 Directory:\n1. Cognition AI - Website: https://cognition.ai. Batch: W24. Category: Developer Tools. Location: San Francisco, CA.\n2. E2B - Website: https://e2b.dev. Batch: W24. Category: AI Infrastructure. Location: San Francisco, CA.\n3. Tavus - Website: https://tavus.io. Batch: W24. Category: Generative Video. Location: San Francisco, CA.",
            retries=0
        )
        src2 = Source(
            id="src_yc_02",
            run_id=run1.id,
            url="https://www.linkedin.com/company/yc",
            domain="linkedin.com",
            title="YC LinkedIn Page",
            policy_status="blocked",
            policy_reason="Domain restricted by system policy.",
            fetch_status="skipped",
            retries=0
        )
        db.add(src1)
        db.add(src2)
        db.flush()

        rec1_1 = Record(
            id="rec_yc_01",
            run_id=run1.id,
            values_json={"company_name": "Cognition AI", "website": "https://cognition.ai", "batch": "W24", "category": "Developer Tools", "location": "San Francisco, CA"},
            status="verified",
            row_confidence=0.98,
            sources_count=1,
            dedupe_key="cognition ai"
        )
        db.add(rec1_1)
        db.flush()

        ev1_1 = Evidence(
            record_id=rec1_1.id,
            column="company_name",
            value="Cognition AI",
            source_id=src1.id,
            quote="Cognition AI - Website: https://cognition.ai",
            quote_verified=True,
            confidence=0.98,
            char_start=30,
            char_end=76
        )
        db.add(ev1_1)

        events1 = [
            RunEvent(run_id=run1.id, stage="PLAN", level="info", message="Loaded extraction plan snapshot.", data_json={"title": "YC W24 Startups Directory"}),
            RunEvent(run_id=run1.id, stage="POLICY", level="warn", message="Blocked restricted domain: linkedin.com", data_json={"url": "https://www.linkedin.com/company/yc"}),
            RunEvent(run_id=run1.id, stage="DISCOVER", level="success", message="Discovered 4 sources across search queries.", data_json={"count": 4}),
            RunEvent(run_id=run1.id, stage="FETCH", level="success", message="Fetched ycombinator.com (1,240 chars text extracted)", data_json={"url": "https://www.ycombinator.com/companies"}),
            RunEvent(run_id=run1.id, stage="EXTRACT", level="success", message="Extracted 3 startup entity candidates.", data_json={"count": 3}),
            RunEvent(run_id=run1.id, stage="VERIFY", level="success", message="Verified verbatim receipts for 3 records.", data_json={"verified": 3}),
            RunEvent(run_id=run1.id, stage="DEDUPE", level="success", message="0 duplicates found. 3 clean records outputted.", data_json={"final_count": 3}),
            RunEvent(run_id=run1.id, stage="FINALIZE", level="success", message="Run completed successfully with 100% quote verification.", data_json={})
        ]
        for ev in events1:
            db.add(ev)

        db.commit()
        print("Successfully seeded demo data for ProofData!")
    except Exception as e:
        db.rollback()
        print(f"Error seeding demo data: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo_data()
