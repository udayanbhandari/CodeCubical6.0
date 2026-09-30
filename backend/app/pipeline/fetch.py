import re
import json
import hashlib
import asyncio
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import httpx
import trafilatura
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import settings
from app.pipeline.policy import check_url

logger = logging.getLogger("proofdata.fetch")

# Per-domain rate limiting timestamps
_DOMAIN_LAST_FETCH: Dict[str, float] = {}
_DOMAIN_LOCKS: Dict[str, asyncio.Lock] = {}


def _extract_json_ld(html: str) -> List[str]:
    """Extract all JSON-LD script blocks from raw HTML."""
    blocks = []
    pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
    for match in matches:
        cleaned = match.strip()
        if cleaned:
            try:
                # Ensure valid JSON formatting
                parsed = json.loads(cleaned)
                blocks.append(json.dumps(parsed, indent=2))
            except Exception:
                blocks.append(cleaned)
    return blocks


async def _fetch_single_source(candidate: Dict[str, Any], ctx: Optional[Any] = None) -> Dict[str, Any]:
    """Fetch a single source with rate limiting, trafilatura text extraction, and Jina fallback."""
    url = candidate["url"]
    domain = candidate.get("domain") or urlparse(url).hostname or "unknown"

    if candidate.get("policy_status") == "blocked":
        if ctx and hasattr(ctx, "emit"):
            await ctx.emit("FETCH", "warn", f"Skipping blocked source: {url}", {"url": url, "reason": candidate.get("policy_reason")})
        return {
            **candidate,
            "fetch_status": "skipped",
            "policy_status": "blocked",
            "text": None,
            "http_status": None,
            "content_hash": None,
            "retries": 0
        }

    # Per-Domain Rate Limiter (1s delay between requests to same domain)
    if domain not in _DOMAIN_LOCKS:
        _DOMAIN_LOCKS[domain] = asyncio.Lock()

    async with _DOMAIN_LOCKS[domain]:
        now = asyncio.get_event_loop().time()
        last_time = _DOMAIN_LAST_FETCH.get(domain, 0)
        elapsed = now - last_time
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _DOMAIN_LAST_FETCH[domain] = asyncio.get_event_loop().time()

    if ctx and hasattr(ctx, "emit"):
        await ctx.emit("FETCH", "info", f"Fetching URL: {url}", {"url": url})

    retries_count = 0
    http_status = None
    raw_html = ""
    extracted_text = ""

    # Fetch via httpx with tenacity retries
    try:
        async with httpx.AsyncClient(
            timeout=settings.FETCH_TIMEOUT,
            headers={"User-Agent": settings.USER_AGENT},
            follow_redirects=True
        ) as client:
            
            for attempt in range(3):  # initial + 2 retries
                try:
                    res = await client.get(url)
                    http_status = res.status_code
                    if res.status_code == 200:
                        raw_html = res.text
                        break
                    else:
                        retries_count += 1
                        if ctx and hasattr(ctx, "emit"):
                            await ctx.emit("FETCH", "warn", f"HTTP {res.status_code} for {url} (Attempt {attempt+1})", {"url": url, "status": res.status_code})
                except Exception as ex:
                    retries_count += 1
                    if attempt == 2:
                        raise ex
                    await asyncio.sleep(1.0 * (2 ** attempt))

            if raw_html:
                # Primary extraction via Trafilatura
                trafilatura_text = trafilatura.extract(
                    raw_html,
                    include_tables=True,
                    include_links=True,
                    include_comments=False
                ) or ""

                extracted_text = trafilatura_text.strip()

                # Fallback to Jina Reader if extracted text is under 300 characters
                if len(extracted_text) < 300:
                    if ctx and hasattr(ctx, "emit"):
                        await ctx.emit("FETCH", "info", f"Text under 300 chars ({len(extracted_text)} chars). Triggering Jina Reader fallback for {url}", {"url": url})
                    try:
                        jina_url = f"https://r.jina.ai/{url}"
                        jina_res = await client.get(jina_url, timeout=15.0)
                        if jina_res.status_code == 200 and len(jina_res.text) > len(extracted_text):
                            extracted_text = jina_res.text.strip()
                    except Exception as jina_err:
                        logger.warning(f"Jina Reader fallback failed for {url}: {jina_err}")

                # Extract JSON-LD blocks and append header if present
                json_ld_blocks = _extract_json_ld(raw_html)
                if json_ld_blocks:
                    json_ld_text = "\n\n[JSON-LD]\n" + "\n".join(json_ld_blocks)
                    extracted_text = (extracted_text + json_ld_text).strip()

    except Exception as err:
        logger.error(f"Failed to fetch {url}: {err}")
        if ctx and hasattr(ctx, "emit"):
            await ctx.emit("FETCH", "error", f"Fetch failed for {url}: {str(err)}", {"url": url, "error": str(err)})
        return {
            **candidate,
            "fetch_status": "failed",
            "http_status": http_status,
            "text": None,
            "content_hash": None,
            "retries": retries_count
        }

    # Truncate at 60,000 characters to prevent memory overflow
    final_text = extracted_text[:60000] if extracted_text else ""
    content_hash = hashlib.md5(final_text.encode("utf-8")).hexdigest() if final_text else None
    fetch_status = "success" if final_text else "failed"

    if ctx and hasattr(ctx, "emit"):
        if fetch_status == "success":
            await ctx.emit("FETCH", "success", f"Successfully fetched {url} ({len(final_text)} chars)", {"url": url, "length": len(final_text)})
        else:
            await ctx.emit("FETCH", "warn", f"Fetched {url} but no readable text extracted", {"url": url})

    return {
        **candidate,
        "fetch_status": fetch_status,
        "http_status": http_status or 200,
        "text": final_text,
        "content_hash": content_hash,
        "retries": retries_count
    }


async def fetch_all(candidates: List[Dict[str, Any]], ctx: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Fetch all candidate URLs concurrently with a max concurrency of 5."""
    semaphore = asyncio.Semaphore(5)

    async def _sem_fetch(candidate: Dict[str, Any]):
        async with semaphore:
            return await _fetch_single_source(candidate, ctx)

    tasks = [_sem_fetch(c) for c in candidates]
    results = await asyncio.gather(*tasks)
    return list(results)
