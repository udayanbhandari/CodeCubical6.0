import pytest
import respx
import httpx
from fastapi.testclient import TestClient
from app.main import app
from app.pipeline.policy import check_url
from app.pipeline.discover import normalize_url, discover_sources
from app.pipeline.fetch import fetch_all
from app.schemas import Plan, ColumnSchema, SourceStrategy


def test_url_normalization():
    raw_url = "HTTPS://WWW.Example.com/path/to/page/?utm_source=twitter&utm_medium=cpc&id=123#fragment"
    norm = normalize_url(raw_url)
    assert norm == "https://www.example.com/path/to/page?id=123"


@pytest.mark.asyncio
async def test_policy_ssrf_rejection():
    # Test private IPs and localhost
    res_loopback = await check_url("http://127.0.0.1/internal")
    assert res_loopback.status == "blocked"
    assert "SSRF" in res_loopback.reason

    res_localhost = await check_url("http://localhost:8000/secret")
    assert res_localhost.status == "blocked"
    assert "SSRF" in res_localhost.reason

    res_scheme = await check_url("file:///etc/passwd")
    assert res_scheme.status == "blocked"
    assert "Disallowed scheme" in res_scheme.reason


@pytest.mark.asyncio
async def test_policy_blocklist():
    res_linkedin = await check_url("https://www.linkedin.com/in/john-doe")
    assert res_linkedin.status == "blocked"
    assert "restricted by system policy" in res_linkedin.reason

    res_allowed = await check_url("https://ycombinator.com/companies")
    assert res_allowed.status == "allowed"


@pytest.mark.asyncio
@respx.mock
async def test_robots_txt_allow_and_deny():
    domain = "https://test-robots-site.com"
    robots_content = """
User-agent: *
Disallow: /private/
Allow: /public/
"""
    respx.get(f"{domain}/robots.txt").mock(return_value=httpx.Response(200, text=robots_content))

    denied = await check_url(f"{domain}/private/data")
    assert denied.status == "blocked"
    assert "robots.txt disallows" in denied.reason

    allowed = await check_url(f"{domain}/public/data")
    assert allowed.status == "allowed"


def test_api_policy_endpoint():
    client = TestClient(app)
    response = client.get("/api/policy")
    assert response.status_code == 200
    data = response.json()
    assert "ssrf_protection" in data
    assert "builtin_blocked_domains" in data
    assert "linkedin.com" in data["builtin_blocked_domains"]
