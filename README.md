# ProofData

AI-Powered Data Intelligence Platform for Hackathons.

## Overview
ProofData allows users to describe a data need in plain English. The AI designs and runs a data-collection workflow from permitted web sources, validates data against strict receipt requirements (verbatim source quote + URL + confidence), and presents verified results in an interactive dashboard.

## Core Principle
Every extracted value **must** carry a receipt:
- Source URL
- Exact supporting verbatim quote
- Confidence score

Any value whose supporting quote does not literally appear in the fetched web page text is strictly rejected.

## Tech Stack
- **Backend**: Python 3.11, FastAPI, SQLAlchemy 2 + SQLite, Pydantic v2, httpx, trafilatura, rapidfuzz, APScheduler, sse-starlette.
- **Frontend**: Next.js 14 (App Router, TypeScript), Tailwind CSS, shadcn/ui, TanStack Query, lucide-react.
- **LLM**: Single wrapper `app/llm.py` with schema-validated outputs and retries.
- **Search & Fetch**: Tavily API (`SearchProvider`) with `httpx` + `trafilatura` + Jina Reader fallback.

## Setup & Running

### 1. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

### 2. Run Local Development
```bash
# Option A: Run dev servers using Makefile
make dev

# Option B: Run backend and frontend manually
# Backend
.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000 --app-dir backend

# Frontend
cd frontend
npm run dev
```

### 3. Docker Compose
```bash
docker-compose up --build
```

### 4. Running Tests
```bash
make test
```
