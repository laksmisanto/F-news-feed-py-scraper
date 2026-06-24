"""
RSS fetcher — uses feedparser to parse RSS/Atom feeds.
Preferred fetcher in the chain: RSS → Sitemap → HTML.
"""

import feedparser
import httpx
from typing import Optional

from fetchers.base import BaseFetcher, HEADERS
from utils.logger import get_logger

logger = get_logger("fetcher.rss")


class RSSFetcher(BaseFetcher):
    name = "rss"

    async def fetch_urls(
        self,
        source,
        client: httpx.AsyncClient,
        max_articles: int = 10
    ) -> list[str]:
        """
        Fetch article URLs from the source's RSS feed.
        Returns empty list on failure (triggers fallback to sitemap).
        """
        if not source.rss_url:
            logger.debug(f"[RSS] No RSS URL configured for source: {source.name}")
            return []

        try:
            response = await client.get(
                source.rss_url,
                headers=HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
            content = response.text
        except Exception as e:
            logger.warning(f"[RSS] Failed to fetch RSS for {source.name}: {e}")
            return []

        try:
            feed = feedparser.parse(content)
        except Exception as e:
            logger.warning(f"[RSS] Failed to parse RSS for {source.name}: {e}")
            return []

        if not feed.entries:
            logger.warning(f"[RSS] Empty feed for {source.name}")
            return []

        urls = []
        for entry in feed.entries[:max_articles]:
            link = getattr(entry, "link", None)
            if link:
                urls.append(link.strip())

        logger.info(f"[RSS] {source.name} → {len(urls)} URLs found")
        return urls
