import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
import httpx
from app.config import settings
from app.schemas import Plan
from app.pipeline.policy import check_url

logger = logging.getLogger("proofdata.discover")


def normalize_url(url: str) -> str:
    """Normalize URL by lowercasing scheme/netloc, removing tracking params, and stripping trailing slashes."""
    try:
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        if not path:
            path = ""

        query_params = parse_qs(parsed.query, keep_blank_values=False)
        # Remove tracking parameters
        clean_params = {
            k: v for k, v in query_params.items()
            if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref")
        }
        clean_query = urlencode(clean_params, doseq=True)

        return urlunparse((scheme, netloc, path, parsed.params, clean_query, ""))
    except Exception:
        return url.strip()


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """Return list of search result dicts: [{'url': ..., 'title': ..., 'snippet': ...}]."""
        pass


class TavilyProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        api_key = settings.TAVILY_API_KEY
        if not api_key or api_key.startswith("your_"):
            logger.warning(f"TAVILY_API_KEY not configured. Returning heuristic mock search results for query: '{query}'")
            return [
                {
                    "url": f"https://example.com/directory/{query.replace(' ', '_')}",
                    "title": f"Directory & Info for {query}",
                    "snippet": f"Official details and directory listing for {query}."
                },
                {
                    "url": f"https://news-portal.org/articles/{query.replace(' ', '_')}",
                    "title": f"News report on {query}",
                    "snippet": f"Latest updates and news coverage about {query}."
                }
            ]

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic"
                    }
                )
                if res.status_code == 200:
                    data = res.json()
                    results = []
                    for item in data.get("results", []):
                        results.append({
                            "url": item.get("url"),
                            "title": item.get("title", ""),
                            "snippet": item.get("content", "")
                        })
                    return results
                else:
                    logger.error(f"Tavily search API returned HTTP {res.status_code}: {res.text}")
                    return []
        except Exception as e:
            logger.error(f"Tavily search request failed: {e}")
            return []


async def discover_sources(
    plan: Plan,
    provider: Optional[SearchProvider] = None,
    ctx: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """Discover candidate URLs via search provider and seed URLs, evaluate policy, and rank candidates."""
    if provider is None:
        provider = TavilyProvider()

    all_raw_urls: Dict[str, Dict[str, Any]] = {}

    # 1. Add Seed URLs
    for seed in plan.source_strategy.seed_urls:
        norm = normalize_url(seed)
        all_raw_urls[norm] = {"url": norm, "title": "Seed URL", "source": "seed"}

    # 2. Run Search Queries Concurrently
    if plan.search_queries:
        tasks = [provider.search(q, max_results=8) for q in plan.search_queries]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)

        for res_list in results_nested:
            if isinstance(res_list, list):
                for item in res_list:
                    url = item.get("url")
                    if url:
                        norm = normalize_url(url)
                        if norm not in all_raw_urls:
                            all_raw_urls[norm] = {
                                "url": norm,
                                "title": item.get("title", ""),
                                "snippet": item.get("snippet", ""),
                                "source": "search"
                            }

    # 3. Policy Check for Candidates
    candidates: List[Dict[str, Any]] = []
    allowed_domains = plan.source_strategy.allowed_domains
    blocked_domains = plan.source_strategy.blocked_domains

    for norm_url, meta in all_raw_urls.items():
        parsed = urlparse(norm_url)
        domain = parsed.hostname or ""
        decision = await check_url(norm_url, allowed_domains=allowed_domains, blocked_domains=blocked_domains)

        candidate_item = {
            "url": norm_url,
            "domain": domain,
            "title": meta.get("title", ""),
            "policy_status": decision.status,
            "policy_reason": decision.reason,
            "fetch_status": "pending" if decision.status == "allowed" else "skipped"
        }
        candidates.append(candidate_item)

    # 4. Rank Candidates
    # Preferred: allowed_domains match > seed_urls > search results > blocked (kept at bottom)
    def ranking_key(c: Dict[str, Any]):
        if c["policy_status"] == "blocked":
            return (9, c["url"])
        is_allowed_domain = any(c["domain"].endswith(ad.lower()) for ad in allowed_domains) if allowed_domains else False
        if is_allowed_domain:
            return (0, c["url"])
        return (1, c["url"])

    candidates.sort(key=ranking_key)

    # 5. Cap at max_pages limit
    limit = min(plan.source_strategy.max_pages, settings.MAX_PAGES_PER_RUN)
    return candidates[:limit]
