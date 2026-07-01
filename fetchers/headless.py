"""
Headless URL discovery fetcher.

Used for SPA/JS-rendered news sites where the crawler (httpx) can't see
article links because the homepage is empty until JavaScript runs.

This fetcher renders seed pages with Playwright, then extracts article
links from the resulting DOM using the same heuristics as the crawler.

When to enable:
    source.requires_browser = True

Configuration is shared with the crawler — uses `source.crawl_config`:
    {
      "seed_paths":           ["/", "/news", "/sports"],
      "playwright_wait":      "networkidle",   # default "domcontentloaded"
      "max_pages_per_run":    20,              # default 20 (rendering is expensive)
      "article_url_patterns": [...],
      "exclude_patterns":     [...]
    }
"""

import asyncio
from typing import Optional
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

from fetchers.base import BaseFetcher
from fetchers.crawler import (
    DEFAULTS as CRAWLER_DEFAULTS,
    CrawlerFetcher,
)
from utils.browser import BrowserContext
from utils.logger import get_logger

logger = get_logger("fetcher.headless")


# Headless-specific defaults. We rely on Playwright rendering, so we don't
# need many seed pages — JavaScript-rendered SPAs typically expose all
# articles via 1-2 entry pages.
HEADLESS_DEFAULTS = {
    "seed_paths": ["/"],
    "playwright_wait": "domcontentloaded",
    "max_pages_per_run": 20,  # rendering is ~5x slower than httpx; lower cap
    "rate_limit_seconds": 1.0,
    "article_url_patterns": None,
    "exclude_patterns": CRAWLER_DEFAULTS["exclude_patterns"],
}


class HeadlessFetcher(BaseFetcher):
    """
    Discovers article URLs on JS-rendered news sites.
    Unlike BaseFetcher's other implementations, this one needs a
    BrowserContext, so we don't use the standard fetch_urls() signature.
    """

    name = "headless"

    async def fetch_urls(
        self,
        source,
        client: httpx.AsyncClient,
        max_articles: int = 10,
    ) -> list[str]:
        """
        Not used. Headless discovery needs a BrowserContext, which
        the standard fetcher interface doesn't carry. Call
        fetch_urls_with_context() instead from the runner.
        """
        raise NotImplementedError(
            "HeadlessFetcher requires a BrowserContext. "
            "Use fetch_urls_with_context() from runner."
        )

    async def fetch_urls_with_context(
        self,
        source,
        ctx: BrowserContext,
        max_articles: int = 10,
    ) -> list[str]:
        """
        Render seed pages with Playwright, extract article URLs.
        """
        cfg = {**HEADLESS_DEFAULTS, **(source.crawl_config or {})}
        base_url = source.base_url.rstrip("/")

        try:
            base_host = urlparse(base_url).netloc.lower()
        except Exception:
            logger.warning(f"[Headless] Invalid base_url for {source.name}: {base_url}")
            return []

        if not base_host:
            return []

        seed_paths = cfg["seed_paths"]
        wait_until = cfg["playwright_wait"]
        max_pages = cfg["max_pages_per_run"]
        rate_sleep = max(0.0, float(cfg.get("rate_limit_seconds", 1.0)))

        article_re = CrawlerFetcher._compile_patterns(cfg["article_url_patterns"])
        exclude_re = CrawlerFetcher._compile_patterns(cfg["exclude_patterns"]) or []

        article_urls: list[str] = []
        seen: set[str] = set()
        pages_rendered = 0

        # Use a tmp CrawlerFetcher instance just for its URL classification logic
        classifier = CrawlerFetcher(timeout=self.timeout)

        for path in seed_paths:
            if len(article_urls) >= max_articles or pages_rendered >= max_pages:
                break

            seed_url = urljoin(base_url + "/", path.lstrip("/"))

            html = await ctx.render(seed_url, wait_until=wait_until)
            pages_rendered += 1
            if rate_sleep > 0:
                await asyncio.sleep(rate_sleep)

            if not html:
                logger.debug(f"[Headless] No HTML rendered for {seed_url}")
                continue

            # Parse links from the rendered DOM
            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception:
                try:
                    soup = BeautifulSoup(html, "html.parser")
                except Exception as e:
                    logger.debug(f"[Headless] Parse error {seed_url}: {e}")
                    continue

            for a in soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue

                try:
                    full = urljoin(seed_url, href).split("#", 1)[0]
                except Exception:
                    continue

                try:
                    if urlparse(full).netloc.lower() != base_host:
                        continue
                except Exception:
                    continue

                if CrawlerFetcher._matches_any(full, exclude_re):
                    continue

                if classifier._is_article_url(full, article_re):
                    if full not in seen:
                        seen.add(full)
                        article_urls.append(full)
                        if len(article_urls) >= max_articles:
                            break

        logger.info(
            f"[Headless] {source.name} → {len(article_urls)} articles "
            f"from {pages_rendered} rendered page(s)"
        )
        return article_urls[:max_articles]
