# ProofData: Demo Guide

Welcome to **ProofData**! This guide walks you through the core workflow of our AI-powered Data Intelligence platform, built for the hackathon. 

## Starting the Platform

1. **Backend**:
   ```bash
   cd backend
   python -m venv venv
   source venv/Scripts/activate # or venv/bin/activate
   pip install -r pyproject/requirements.txt
   uvicorn app.main:app --reload --port 8000
   ```

2. **Frontend**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

3. **Navigate**: Open [http://localhost:3000](http://localhost:3000)

## Demo Scenario

### 1. New Collection
1. On the home page, select one of the **Examples** (e.g. "Sponsor leads SaaS").
2. Click **Generate Plan**. The LLM will design a structured plan containing:
   - Target Columns (with data types and descriptions)
   - Search Queries (to discover pages)
   - Source Strategy (max pages, blocked domains)
3. Optionally adjust columns manually in the **Plan Editor**, then click **Approve & Run**.

### 2. Live Run Timeline
1. You will be routed to `/runs/<run_id>`.
2. Notice the **Live Event Log** showing real-time SSE updates. You'll see the 8 stages:
   `PLAN -> POLICY -> DISCOVER -> FETCH -> EXTRACT -> VERIFY -> DEDUPE -> FINALIZE`
3. If any stage errors, it will prominently show in red.

### 3. Reviewing Results
1. Click the **Results** tab once extraction completes.
2. The table shows extracted records. Notice the strict **Confidence Score**. 
3. **Receipt-Gate Verification**: Click on a record row to open the **Evidence Viewer**.
   - See the exact **supporting verbatim quote** that the LLM pulled.
   - See the source URL.
   - If the quote wasn't physically found in the scraped text via fuzzy matching, the record is flagged or rejected.

### 4. Ask the Dataset
1. At the bottom of the Results tab, use the **Ask the Dataset** chat box.
2. Ask a question like "Which company has the highest funding?"
3. The AI answers strictly using the verified records from this specific run, showing reference IDs.

### 5. Quality & Diff
1. Check the **Quality** tab to view Funnel Charts and Record Distribution.
2. Save the plan as a **Workflow**, schedule it with cron, and run it a second time.
3. Once a second run completes, the **Diff** tab will show what records were `Added`, `Removed`, or `Changed` compared to the prior run.

## Security Controls
Check the **Policy** tab to view the hardcoded scraping rules (SSRF protection against localhost/127.0.0.1, robots.txt enforcement, rate-limits). These operate globally regardless of the user's plan.
