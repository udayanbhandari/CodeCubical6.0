You are building "ProofData", an AI-powered data intelligence platform for a hackathon
(problem: users describe a data need in plain English; the AI designs and runs a
data-collection workflow from permitted sources, validates the data, and shows results
in a dashboard with history and export).

CORE PRINCIPLE: every extracted value must carry a receipt: source URL, exact supporting
quote, and confidence. A value whose quote does not literally appear in the fetched page
text is rejected. Never let the LLM invent data.

STACK
- Backend: Python 3.11, FastAPI, SQLAlchemy 2 + SQLite, Pydantic v2, httpx, trafilatura,
  rapidfuzz, APScheduler, sse-starlette.
- Frontend: Next.js 14 (App Router, TypeScript), Tailwind, shadcn/ui, TanStack Query, lucide-react.
- LLM: one wrapper module `llm.py` with `generate_json(system, user, schema)` that returns
  validated Pydantic output, retries on invalid JSON (max 2), and reads provider/model/key
  from env. No other file talks to an LLM directly.
- Search: Tavily API (behind a `SearchProvider` interface so it can be swapped).
- Fetch: httpx first, trafilatura for text extraction, Jina Reader (r.jina.ai) as fallback.

ARCHITECTURE (pipeline stages, each emits events to a run timeline)
1 PLAN -> 2 POLICY CHECK -> 3 DISCOVER -> 4 FETCH -> 5 EXTRACT -> 6 VERIFY -> 7 DEDUPE/VALIDATE -> 8 FINALIZE
The plan is JSON, saved, editable and re-runnable. The engine is generic: no per-topic scrapers.

RULES
- Small, typed, well-named modules. No dead code, no placeholder TODOs.
- Every pipeline stage is an async function that takes a RunContext and emits events via
  `ctx.emit(stage, level, message, data)`.
- Every external call has timeouts, retries with backoff, and a clear failure event.
- Config only via .env (see .env.example). Never hardcode keys.
- Write pytest tests for verification, dedupe, and policy logic.
- After each task, tell me exactly how to run and test it.

Repo layout:
/backend  (app/main.py, app/config.py, app/db.py, app/models.py, app/schemas.py, app/llm.py,
           app/pipeline/{planner,policy,discover,fetch,extract,verify,dedupe,orchestrator}.py,
           app/routers/, app/events.py, tests/)
/frontend (Next.js app)
/docker-compose.yml, README.md

Acknowledge with a 5-line summary of the plan. Do not write code yet.