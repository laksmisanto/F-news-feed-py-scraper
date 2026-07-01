"""
Domain crawler fetcher — discovers article URLs by BFS-crawling a news site.

When to use:
  - The source has no RSS feed
  - The source has no sitemap
  - You want supplemental discovery (RSS may miss some articles)
  - For any source where you toggle source.crawl_enabled = True

What it does:
  1. Start from seed paths (homepage + configured category pages)
  2. BFS to max_depth, following internal links
  3. Respect robots.txt (Allow/Disallow rules for User-Agent)
  4. Rate-limit per request (configurable per source)
  5. Filter URLs by article_url_patterns (regex)
  6. Skip exclude_patterns (tag/author/archive pages)
  7. Auto-detect article-like URLs when no patterns configured
  8. Cap by max_pages_per_run (hard safety limit)
  9. Return up to max_articles unique article URLs

What it does NOT do:
  - Render JavaScript (use Playwright for SPAs; out of scope here)
  - Extract article content (that's scrapers/article.py's job)
  - Dedupe against the DB (runner.py handles that downstream)

Config shape (source.crawl_config), all keys optional:
{
  "seed_paths":           ["/", "/sports", "/business"],
  "max_depth":            2,
  "max_pages_per_run":    100,
  "rate_limit_seconds":   1.0,
  "article_url_patterns": ["/article/", "/\\d{4}/\\d{2}/"],
  "exclude_patterns":     ["/tag/", "/author/", "/page/", "/category/"],
  "respect_robots":       true
}
"""

import asyncio
import re
from collections import deque
from typing import Optional
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from fetchers.base import BaseFetcher, HEADERS
from utils.logger import get_logger

logger = get_logger("fetcher.crawler")


# ─── Defaults applied when source.crawl_config is None or partial ─────────
DEFAULTS = {
    "seed_paths": ["/"],
    "max_depth": 2,
    "max_pages_per_run": 100,
    "rate_limit_seconds": 1.0,
    # When None, the crawler uses _looks_like_article() heuristic.
    "article_url_patterns": None,
    "exclude_patterns": [
        "/tag/", "/tags/",
        "/author/", "/authors/",
        "/page/", "/p/",
        "/category/", "/categories/",
        "/search", "/login", "/register",
        "/contact", "/about", "/privacy", "/terms",
        "/feed", "/rss", "/sitemap",
        "/wp-admin/", "/wp-login", "/wp-json/",
        "/cdn-cgi/",
        "/amp/", "?amp",
        "/print/",
        "#",
    ],
    "respect_robots": True,
}

# ─── Heuristic: URL patterns that almost always mean "article page" ───────
# Date-in-path (YYYY/MM/DD or YYYY-MM-DD) and common slug separators.
ARTICLE_HEURISTIC_PATTERNS = [
    re.compile(r"/\d{4}/\d{1,2}/\d{1,2}/"),     # /2026/06/26/
    re.compile(r"/\d{4}-\d{1,2}-\d{1,2}"),       # /2026-06-26
    re.compile(r"/article[s]?/"),
    re.compile(r"/news/.+-\d"),                  # /news/some-slug-123456
    re.compile(r"/story[/-]"),
    re.compile(r"/post[/-]"),
    re.compile(r"-\d{5,}(?:\.html|/?$)"),        # slug-123456 or .html
    re.compile(r"/\d{5,}(?:[/?#]|$)"),           # /category/659092 (BD TV portals)
    re.compile(r"\.html$"),
]


class CrawlerFetcher(BaseFetcher):
    """BFS domain crawler. Slots into the fetcher chain like RSS/Sitemap."""

    name = "crawler"

    async def fetch_urls(
        self,
        source,
        client: httpx.AsyncClient,
        max_articles: int = 10,
    ) -> list[str]:
        """
        Crawl source.base_url and return up to max_articles article URLs.
        Empty list if crawler disabled or nothing discovered.
        """
        if not getattr(source, "crawl_enabled", False):
            logger.debug(f"[Crawler] crawl_enabled=False, skipping {source.name}")
            return []

        # Merge config with defaults
        cfg = {**DEFAULTS, **(source.crawl_config or {})}

        base_url = source.base_url.rstrip("/")
        try:
            base_host = urlparse(base_url).netloc.lower()
        except Exception:
            logger.warning(f"[Crawler] Invalid base_url for {source.name}: {base_url}")
            return []

        if not base_host:
            logger.warning(f"[Crawler] Empty host for {source.name}: {base_url}")
            return []

        # ── Robots.txt ─────────────────────────────────────────────────────
        robots = None
        if cfg["respect_robots"]:
            robots = await self._load_robots(base_url, client)

        # ── BFS state ──────────────────────────────────────────────────────
        # Queue holds (url, depth). Visited prevents re-fetching pages.
        queue: deque[tuple[str, int]] = deque()
        visited: set[str] = set()
        article_urls: list[str] = []
        article_seen: set[str] = set()
        pages_fetched = 0

        # Seed URLs
        for path in cfg["seed_paths"]:
            seed = urljoin(base_url + "/", path.lstrip("/"))
            if seed not in visited:
                queue.append((seed, 0))
                visited.add(seed)

        max_pages = cfg["max_pages_per_run"]
        max_depth = cfg["max_depth"]
        rate_sleep = max(0.0, float(cfg["rate_limit_seconds"]))

        article_re = self._compile_patterns(cfg["article_url_patterns"])
        exclude_re = self._compile_patterns(cfg["exclude_patterns"]) or []

        # ── BFS loop ───────────────────────────────────────────────────────
        while queue and len(article_urls) < max_articles and pages_fetched < max_pages:
            url, depth = queue.popleft()

            # robots.txt check
            if robots and not self._robots_allows(robots, url):
                logger.debug(f"[Crawler] robots.txt blocked: {url}")
                continue

            # Fetch page
            content = await self.get(url, client)
            pages_fetched += 1
            if rate_sleep > 0:
                await asyncio.sleep(rate_sleep)

            if not content:
                continue

            # Parse links
            try:
                soup = BeautifulSoup(content, "lxml")
            except Exception:
                try:
                    soup = BeautifulSoup(content, "html.parser")
                except Exception as e:
                    logger.debug(f"[Crawler] Parse error {url}: {e}")
                    continue

            for a in soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue

                # Build absolute URL, drop fragments
                try:
                    full = urljoin(url, href).split("#", 1)[0]
                except Exception:
                    continue

                # Same-host check
                try:
                    if urlparse(full).netloc.lower() != base_host:
                        continue
                except Exception:
                    continue

                # Hard excludes
                if self._matches_any(full, exclude_re):
                    continue

                # Article match?
                is_article = self._is_article_url(full, article_re)

                if is_article:
                    if full not in article_seen:
                        article_seen.add(full)
                        article_urls.append(full)
                        if len(article_urls) >= max_articles:
                            break
                else:
                    # Listing/category-like page → enqueue for further crawl
                    if depth + 1 <= max_depth and full not in visited:
                        visited.add(full)
                        queue.append((full, depth + 1))

        logger.info(
            f"[Crawler] {source.name} → {len(article_urls)} articles "
            f"from {pages_fetched} page(s) (depth≤{max_depth})"
        )
        return article_urls[:max_articles]

    # -------------------------------------------------------------------
    # robots.txt
    # -------------------------------------------------------------------

    async def _load_robots(
        self, base_url: str, client: httpx.AsyncClient
    ) -> Optional[RobotFileParser]:
        """Fetch and parse robots.txt for the host. Returns None on failure."""
        robots_url = urljoin(base_url + "/", "/robots.txt")
        try:
            r = await client.get(
                robots_url,
                headers=HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
            )
            if r.status_code >= 400:
                return None
            text = r.text
        except Exception as e:
            logger.debug(f"[Crawler] robots.txt fetch failed for {base_url}: {e}")
            return None

        try:
            rp = RobotFileParser()
            rp.parse(text.splitlines())
            return rp
        except Exception as e:
            logger.debug(f"[Crawler] robots.txt parse failed for {base_url}: {e}")
            return None

    def _robots_allows(self, robots: RobotFileParser, url: str) -> bool:
        """Use the User-Agent we send in HEADERS."""
        try:
            ua = HEADERS.get("User-Agent", "*")
            return robots.can_fetch(ua, url)
        except Exception:
            return True  # If robots check itself crashes, default to allow

    # -------------------------------------------------------------------
    # URL classification
    # -------------------------------------------------------------------

    @staticmethod
    def _compile_patterns(patterns: Optional[list[str]]) -> Optional[list[re.Pattern]]:
        if not patterns:
            return None
        out = []
        for p in patterns:
            try:
                out.append(re.compile(p, re.IGNORECASE))
            except re.error:
                # Treat invalid regex as plain substring
                out.append(re.compile(re.escape(p), re.IGNORECASE))
        return out

    @staticmethod
    def _matches_any(url: str, patterns: list[re.Pattern]) -> bool:
        for p in patterns:
            if p.search(url):
                return True
        return False

    def _is_article_url(
        self, url: str, configured: Optional[list[re.Pattern]]
    ) -> bool:
        """
        Decide whether a URL points to an article.
        Priority:
          1. If source has configured article_url_patterns → strict match
          2. Else use the heuristic patterns (date-in-path etc.)
          3. Reject if path is too short to be an article slug
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return False

        path = parsed.path or ""
        if not path or path in ("/", ""):
            return False

        # Too short to be an article slug (e.g. "/sports", "/news")
        segments = [s for s in path.split("/") if s]
        if len(segments) < 2 and not any(c.isdigit() for c in path):
            return False

        if configured:
            return self._matches_any(url, configured)

        # Heuristic fallback
        for pat in ARTICLE_HEURISTIC_PATTERNS:
            if pat.search(path):
                return True

        # Heuristic: long slug with hyphens is usually an article
        last_seg = segments[-1] if segments else ""
        if last_seg and "-" in last_seg and len(last_seg) > 20:
            return True

        return False
