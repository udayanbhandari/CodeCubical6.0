import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.llm import _clean_json_text
from app.pipeline.planner import build_plan
from app.schemas import Plan


def test_clean_json_text():
    raw_markdown = """```json
    {
      "key": "value"
    }
    ```"""
    cleaned = _clean_json_text(raw_markdown)
    assert cleaned == '{\n      "key": "value"\n    }'


@pytest.mark.asyncio
async def test_build_plan_generation():
    prompt = "Find open AI research grants for 2024 with application deadline and award amounts"
    plan = await build_plan(prompt)

    assert isinstance(plan, Plan)
    assert len(plan.columns) >= 4
    assert len(plan.search_queries) >= 2
    assert "linkedin.com" in plan.source_strategy.blocked_domains
    assert "facebook.com" in plan.source_strategy.blocked_domains
    assert any(rule.rule == "url_reachable" for rule in plan.validation_rules)


def test_api_plan_endpoint():
    client = TestClient(app)
    response = client.post("/api/plan", json={"prompt": "Extract top YC startups W24 with website and founders"})
    
    assert response.status_code == 200
    data = response.json()
    assert "entity_type" in data
    assert "columns" in data
    assert "search_queries" in data
    assert len(data["columns"]) > 0
