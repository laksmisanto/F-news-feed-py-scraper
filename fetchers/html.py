"""
HTML / domain scraper — last resort fallback.
Scrapes the homepage or a news listing page using CSS selectors
defined in source.html_scrape_config (stored in DB as JSON).

Expected html_scrape_config shape:
{
    "listing_url": "https://example.com/news",   # optional, defaults to base_url
    "article_list": "a.headline",                # CSS selector for article links
    "base_url_prefix": "https://example.com"     # optional, prepend to relative URLs
}
"""

import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from fetchers.base import BaseFetcher, HEADERS
from utils.logger import get_logger

logger = get_logger("fetcher.html")


class HTMLFetcher(BaseFetcher):
    name = "html"

    async def fetch_urls(
        self,
        source,
        client: httpx.AsyncClient,
        max_articles: int = 10
    ) -> list[str]:
        """
        Scrape a listing page for article URLs using CSS selectors.
        Falls back to auto-detection if no config provided.
        """
        config = source.html_scrape_config or {}
        listing_url = config.get("listing_url") or source.base_url
        article_selector = config.get("article_list", "a")
        base_url = config.get("base_url_prefix") or source.base_url

        content = await self.get(listing_url, client)
        if not content:
            logger.warning(f"[HTML] Could not fetch listing page for {source.name}")
            return []

        try:
            soup = BeautifulSoup(content, "lxml")
        except Exception:
            soup = BeautifulSoup(content, "html.parser")

        anchors = soup.select(article_selector)
        if not anchors:
            # Fallback: grab all <a> tags that look like article links
            anchors = soup.find_all("a", href=True)

        urls = []
        seen = set()

        for a in anchors:
            href = a.get("href", "").strip()
            if not href or href.startswith("#") or href.startswith("javascript"):
                continue

            # Build absolute URL
            full_url = urljoin(base_url, href)

            # Basic article URL filter: must contain a path segment
            if full_url in seen:
                continue
            if not self._looks_like_article(full_url, source.base_url):
                continue

            seen.add(full_url)
            urls.append(full_url)

            if len(urls) >= max_articles:
                break

        logger.info(f"[HTML] {source.name} → {len(urls)} URLs found")
        return urls

    def _looks_like_article(self, url: str, base_url: str) -> bool:
        """
        Heuristic filter: URL should be from same domain and look like an article.
        """
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            base_parsed = urlparse(base_url)

            # Must be same domain
            if parsed.netloc and base_parsed.netloc:
                if base_parsed.netloc not in parsed.netloc:
                    return False

            path = parsed.path
            # Must have a non-trivial path
            if not path or path in ("/", ""):
                return False

            # Skip common non-article paths
            skip_patterns = [
                "/category/", "/tag/", "/author/", "/page/",
                "/search", "/login", "/register", "/contact",
                "/about", "/sitemap", ".xml", ".rss", ".json",
                "/feed", "/cdn-cgi/", "/wp-admin/",
            ]
            for pattern in skip_patterns:
                if pattern in path.lower():
                    return False

            return True
        except Exception:
            return False
