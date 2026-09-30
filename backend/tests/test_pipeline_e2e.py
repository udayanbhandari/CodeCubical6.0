import re
import pytest
import respx
import httpx
import asyncio
from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, engine
from app.schemas import Plan, ColumnSchema, SourceStrategy


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.mark.asyncio
@respx.mock
async def test_full_pipeline_e2e():
    target_url = "https://ycombinator.com/companies/test-ai"
    page_html = "<html><body><h1>Test AI</h1><p>Test AI was founded in 2024. Visit us at https://testai.com.</p></body></html>"

    respx.get(target_url).mock(return_value=httpx.Response(200, text=page_html))
    respx.get("https://ycombinator.com/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /"))
    respx.get(re.compile(r"https://r\.jina\.ai/.*")).mock(return_value=httpx.Response(200, text="Test AI was founded in 2024. Visit us at https://testai.com."))

    plan = Plan(
        title="E2E Startup Plan",
        objective="Extract startup facts",
        entity_type="Company",
        columns=[
            ColumnSchema(name="name", type="string", description="Company name", required=True),
            ColumnSchema(name="website", type="url", description="Website URL", required=True)
        ],
        unique_key_columns=["name"],
        search_queries=["Test AI startup"],
        source_strategy=SourceStrategy(
            preferred_source_types=["official"],
            seed_urls=[target_url],
            allowed_domains=["ycombinator.com"],
            blocked_domains=["linkedin.com"],
            max_pages=2
        ),
        extraction_instructions="Extract company facts",
        validation_rules=[]
    )

    client = TestClient(app)

    # 1. Trigger Run Creation API
    response = client.post("/api/runs", json={"plan": plan.model_dump()})
    assert response.status_code == 200
    run_data = response.json()
    run_id = run_data["id"]

    # Wait for background pipeline task execution
    await asyncio.sleep(2.5)

    # 2. Check Run Status
    get_run_res = client.get(f"/api/runs/{run_id}")
    assert get_run_res.status_code == 200
    r_info = get_run_res.json()
    assert r_info["status"] in ("completed", "running")

    # 3. Check Records API
    recs_res = client.get(f"/api/runs/{run_id}/records")
    assert recs_res.status_code == 200
    recs = recs_res.json()
    assert len(recs) >= 1
    assert recs[0]["status"] in ("verified", "needs_review")

    # 4. Check CSV Export API
    export_res = client.get(f"/api/runs/{run_id}/export?format=csv")
    assert export_res.status_code == 200
    assert "text/csv" in export_res.headers["content-type"]
    assert "\ufeff" in export_res.text  # UTF-8 BOM present
