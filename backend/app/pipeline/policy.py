import ipaddress
import socket
import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from typing import Optional, NamedTuple, Dict
import httpx
from app.config import settings

logger = logging.getLogger("proofdata.policy")

# In-memory robots.txt parser cache per domain
_ROBOTS_CACHE: Dict[str, Optional[RobotFileParser]] = {}
_BUILTIN_BLOCKED_DOMAINS = {"linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com"}


class PolicyDecision(NamedTuple):
    status: str  # allowed | blocked | api_used | skipped
    reason: str


def _is_private_ip(hostname: str) -> bool:
    """SSRF Guard: Check if hostname resolves to a loopback, private, link-local, or multicast IP."""
    if hostname.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
    except ValueError:
        pass

    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                return True
    except socket.gaierror:
        # Unable to resolve hostname
        return False
    except Exception as e:
        logger.warning(f"DNS resolution error for SSRF check on {hostname}: {e}")
        return False

    return False


async def check_url(
    url: str,
    allowed_domains: Optional[list[str]] = None,
    blocked_domains: Optional[list[str]] = None
) -> PolicyDecision:
    """Evaluate URL against SSRF guards, domain blocklists, allowlists, and robots.txt."""
    parsed = urlparse(url)

    # 1. Scheme Check
    if parsed.scheme.lower() not in ("http", "https"):
        return PolicyDecision("blocked", f"Disallowed scheme '{parsed.scheme}'. Only http and https are permitted.")

    hostname = parsed.hostname
    if not hostname:
        return PolicyDecision("blocked", "Invalid URL: missing domain hostname.")

    # 2. SSRF Protection
    if _is_private_ip(hostname):
        return PolicyDecision("blocked", f"SSRF Guard: Access to private/loopback IP or host '{hostname}' is forbidden.")

    domain_lower = hostname.lower()

    # 3. Domain Allow/Block Policy
    all_blocked = set(_BUILTIN_BLOCKED_DOMAINS)
    if blocked_domains:
        all_blocked.update(d.lower() for d in blocked_domains)

    for blocked in all_blocked:
        if domain_lower == blocked or domain_lower.endswith(f".{blocked}"):
            return PolicyDecision("blocked", f"Domain '{domain_lower}' is restricted by system policy or plan configuration.")

    if allowed_domains and len(allowed_domains) > 0:
        allowed_match = any(
            domain_lower == a.lower() or domain_lower.endswith(f".{a.lower()}")
            for a in allowed_domains
        )
        if not allowed_match:
            return PolicyDecision("blocked", f"Domain '{domain_lower}' is not in the plan allowed_domains list.")

    # 4. Robots.txt Check (Cached per domain)
    robot_parser = _ROBOTS_CACHE.get(domain_lower)
    if robot_parser is None and domain_lower not in _ROBOTS_CACHE:
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        try:
            async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": settings.USER_AGENT}) as client:
                res = await client.get(robots_url)
                if res.status_code == 200:
                    rp.parse(res.text.splitlines())
                    _ROBOTS_CACHE[domain_lower] = rp
                else:
                    _ROBOTS_CACHE[domain_lower] = None
        except Exception:
            _ROBOTS_CACHE[domain_lower] = None
        robot_parser = _ROBOTS_CACHE.get(domain_lower)

    if robot_parser:
        user_agent = settings.USER_AGENT.split("/")[0]  # e.g., "ProofDataBot"
        if not robot_parser.can_fetch(user_agent, url) and not robot_parser.can_fetch("*", url):
            return PolicyDecision("blocked", f"robots.txt disallows path '{parsed.path}' on {domain_lower}")

    return PolicyDecision("allowed", "URL passed all policy, SSRF, and robots.txt checks.")


def get_policy_rules_description() -> dict:
    """Return plain-English summary of active system policy rules for UI."""
    return {
        "ssrf_protection": "Strict rejection of loopback (127.0.0.1, localhost), RFC1918 private subnets, and non-HTTP schemes.",
        "builtin_blocked_domains": sorted(list(_BUILTIN_BLOCKED_DOMAINS)),
        "robots_txt_enforcement": "Automatic per-domain robots.txt parsing and path compliance checking.",
        "user_agent": settings.USER_AGENT,
        "max_pages_per_run": settings.MAX_PAGES_PER_RUN,
        "fetch_timeout_seconds": settings.FETCH_TIMEOUT,
    }
