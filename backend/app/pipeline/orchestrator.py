import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models import Run, Source, Record, Evidence, RunDiff, Workflow
from app.schemas import Plan
from app.events import RunContext
from app.pipeline.discover import discover_sources
from app.pipeline.fetch import fetch_all
from app.pipeline.extract import extract_all_sources
from app.pipeline.verify import verify_record
from app.pipeline.dedupe import dedupe_records, generate_quality_report, generate_dedupe_key

logger = logging.getLogger("proofdata.orchestrator")
_ORCHESTRATOR_SEMAPHORE = asyncio.Semaphore(2)
_CANCELLED_RUNS = set()


def cancel_run_execution(run_id: str):
    """Flag a run for immediate cancellation."""
    _CANCELLED_RUNS.add(run_id)


def is_run_cancelled(run_id: str) -> bool:
    return run_id in _CANCELLED_RUNS


def clean_orphaned_runs():
    """Mark any orphaned 'running' runs as failed on system startup."""
    db: Session = SessionLocal()
    try:
        orphaned = db.query(Run).filter(Run.status == "running").all()
        for run in orphaned:
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error = "Run orphaned due to system restart."
        db.commit()
    except Exception as e:
        logger.error(f"Error cleaning orphaned runs: {e}")
    finally:
        db.close()


def _compute_run_diff(current_records: List[Dict[str, Any]], prev_records: List[Record], plan: Plan) -> Dict[str, List[Any]]:
    """Compute added, removed, and changed records vs previous run."""
    prev_map = {r.dedupe_key: r for r in prev_records if r.dedupe_key}
    curr_map = {r["dedupe_key"]: r for r in current_records if r.get("dedupe_key")}

    added = []
    removed = []
    changed = []

    for d_key, curr_rec in curr_map.items():
        if d_key not in prev_map:
            added.append(curr_rec["values_json"])
        else:
            prev_rec = prev_map[d_key]
            prev_vals = prev_rec.values_json or {}
            curr_vals = curr_rec["values_json"] or {}

            diff_cells = {}
            for col in plan.columns:
                c_name = col.name
                old_v = prev_vals.get(c_name)
                new_v = curr_vals.get(c_name)
                if str(old_v) != str(new_v):
                    diff_cells[c_name] = {"old": old_v, "new": new_v}

            if diff_cells:
                changed.append({
                    "dedupe_key": d_key,
                    "changes": diff_cells
                })

    for d_key, prev_rec in prev_map.items():
        if d_key not in curr_map:
            removed.append(prev_rec.values_json)

    return {
        "added": added,
        "removed": removed,
        "changed": changed
    }


async def execute_run(run_id: str):
    """Execute full 8-stage data extraction pipeline for a given run."""
    async with _ORCHESTRATOR_SEMAPHORE:
        ctx = RunContext(run_id)
        db: Session = SessionLocal()

        try:
            run_db = db.query(Run).filter(Run.id == run_id).first()
            if not run_db:
                logger.error(f"Run {run_id} not found in database.")
                return

            run_db.status = "running"
            run_db.started_at = datetime.now(timezone.utc)
            db.commit()

            plan = Plan.model_validate(run_db.plan_snapshot)
            await ctx.emit("PLAN", "info", f"Loaded plan snapshot for '{plan.title}'", {"plan": plan.model_dump()})

            if is_run_cancelled(run_id):
                raise asyncio.CancelledError("Run cancelled by user.")

            # Stage 2: POLICY & DISCOVER
            await ctx.emit("DISCOVER", "info", f"Discovering candidate URLs using {len(plan.search_queries)} search queries.", {})
            candidates = await discover_sources(plan, ctx=ctx)
            await ctx.emit("DISCOVER", "success", f"Discovered {len(candidates)} candidate URLs.", {"count": len(candidates)})

            # Save discovered source rows
            source_db_map: Dict[str, Source] = {}
            for c in candidates:
                src_obj = Source(
                    run_id=run_id,
                    url=c["url"],
                    domain=c["domain"],
                    title=c.get("title"),
                    policy_status=c["policy_status"],
                    policy_reason=c.get("policy_reason"),
                    fetch_status=c["fetch_status"]
                )
                db.add(src_obj)
                db.flush()
                source_db_map[c["url"]] = src_obj
                c["id"] = src_obj.id
            db.commit()

            if is_run_cancelled(run_id):
                raise asyncio.CancelledError("Run cancelled by user.")

            # Stage 3: FETCH
            await ctx.emit("FETCH", "info", "Fetching allowed sources with rate-limiting and fallback extraction.", {})
            fetched_candidates = await fetch_all(candidates, ctx=ctx)

            for fc in fetched_candidates:
                src_db = source_db_map.get(fc["url"])
                if src_db:
                    src_db.fetch_status = fc["fetch_status"]
                    src_db.http_status = fc.get("http_status")
                    src_db.text = fc.get("text")
                    src_db.content_hash = fc.get("content_hash")
                    src_db.fetched_at = datetime.now(timezone.utc)
                    src_db.retries = fc.get("retries", 0)
            db.commit()

            if is_run_cancelled(run_id):
                raise asyncio.CancelledError("Run cancelled by user.")

            # Stage 4 & 5: EXTRACT & VERIFY
            await ctx.emit("EXTRACT", "info", "Extracting candidate entities from fetched text.", {})
            extracted_raw = await extract_all_sources(fetched_candidates, plan, ctx=ctx)

            verified_records: List[Dict[str, Any]] = []
            for item in extracted_raw:
                src_id = item["source_id"]
                src_text = item["source_text"]
                raw_rec = item["raw_record"]

                v_rec = verify_record(raw_rec, src_text, src_id, plan)
                verified_records.append(v_rec)

            await ctx.emit("VERIFY", "success", f"Verified receipts for {len(verified_records)} extracted entity candidates.", {"count": len(verified_records)})

            if is_run_cancelled(run_id):
                raise asyncio.CancelledError("Run cancelled by user.")

            # Stage 6: DEDUPE & MERGE
            await ctx.emit("DEDUPE", "info", "Deduplicating and merging records across sources.", {})
            final_records = dedupe_records(verified_records, plan)

            # Persist Final Records and Evidence Items to Database
            for frec in final_records:
                rec_db = Record(
                    run_id=run_id,
                    values_json=frec["values_json"],
                    status=frec["status"],
                    row_confidence=frec["row_confidence"],
                    sources_count=frec["sources_count"],
                    dedupe_key=frec.get("dedupe_key"),
                    conflict_json=frec.get("conflict_json"),
                    rejection_reason=frec.get("rejection_reason")
                )
                db.add(rec_db)
                db.flush()

                for ev in frec.get("evidence_items", []):
                    ev_db = Evidence(
                        record_id=rec_db.id,
                        column=ev["column"],
                        value=ev.get("value"),
                        source_id=ev.get("source_id"),
                        quote=ev["quote"],
                        quote_verified=ev["quote_verified"],
                        confidence=ev["confidence"],
                        char_start=ev.get("char_start"),
                        char_end=ev.get("char_end")
                    )
                    db.add(ev_db)
            db.commit()

            # Stage 7: DIFF VS PREVIOUS COMPLETED RUN
            if run_db.workflow_id:
                prev_run = db.query(Run).filter(
                    Run.workflow_id == run_db.workflow_id,
                    Run.status == "completed",
                    Run.id != run_id
                ).order_by(Run.finished_at.desc()).first()

                if prev_run:
                    prev_recs = db.query(Record).filter(Record.run_id == prev_run.id).all()
                    diff_res = _compute_run_diff(final_records, prev_recs, plan)
                    rdiff = RunDiff(
                        run_id=run_id,
                        previous_run_id=prev_run.id,
                        added_json=diff_res["added"],
                        removed_json=diff_res["removed"],
                        changed_json=diff_res["changed"]
                    )
                    db.add(rdiff)
                    db.commit()
                    await ctx.emit("FINALIZE", "info", f"Diff computed vs run {prev_run.id}: +{len(diff_res['added'])}, -{len(diff_res['removed'])}, ~{len(diff_res['changed'])}", {})

            # Stage 8: FINALIZE & QUALITY REPORT
            stats = generate_quality_report(fetched_candidates, len(verified_records), final_records, plan)
            run_db.status = "completed"
            run_db.finished_at = datetime.now(timezone.utc)
            run_db.stats_json = stats
            db.commit()

            await ctx.emit("FINALIZE", "success", f"Run completed successfully. {stats['verified_count']} verified records outputted.", {"stats": stats})

        except asyncio.CancelledError:
            db.rollback()
            run_db = db.query(Run).filter(Run.id == run_id).first()
            if run_db:
                run_db.status = "cancelled"
                run_db.finished_at = datetime.now(timezone.utc)
                run_db.error = "Run cancelled by user request."
                db.commit()
            await ctx.emit("FINALIZE", "warn", "Run execution cancelled by user.", {})
        except Exception as e:
            logger.error(f"Execution error in run {run_id}: {e}", exc_info=True)
            db.rollback()
            run_db = db.query(Run).filter(Run.id == run_id).first()
            if run_db:
                run_db.status = "failed"
                run_db.finished_at = datetime.now(timezone.utc)
                run_db.error = str(e)
                db.commit()
            await ctx.emit("FINALIZE", "error", f"Run failed with error: {str(e)}", {"error": str(e)})
        finally:
            if run_id in _CANCELLED_RUNS:
                _CANCELLED_RUNS.remove(run_id)
            db.close()
