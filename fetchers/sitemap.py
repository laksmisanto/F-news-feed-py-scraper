"""
Sitemap fetcher — parses XML sitemaps (sitemap index + urlset).
Fallback when RSS is unavailable or fails.
Supports nested sitemap indexes.
"""

import httpx
from lxml import etree
from typing import Optional
from datetime import datetime, timedelta

from fetchers.base import BaseFetcher, HEADERS
from utils.logger import get_logger

logger = get_logger("fetcher.sitemap")

# Sitemap XML namespaces
NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
}

# Only consider URLs from the last N hours to avoid old articles
RECENCY_HOURS = 48


class SitemapFetcher(BaseFetcher):
    name = "sitemap"

    async def fetch_urls(
        self,
        source,
        client: httpx.AsyncClient,
        max_articles: int = 10
    ) -> list[str]:
        """
        Fetch article URLs from the source's sitemap.
        Handles both sitemap index files and direct urlset sitemaps.
        """
        if not source.sitemap_url:
            logger.debug(f"[Sitemap] No sitemap URL for source: {source.name}")
            return []

        content = await self.get(source.sitemap_url, client)
        if not content:
            return []

        try:
            root = etree.fromstring(content.encode("utf-8"))
        except Exception:
            try:
                root = etree.fromstring(content.encode("utf-8"), parser=etree.XMLParser(recover=True))
            except Exception as e:
                logger.warning(f"[Sitemap] XML parse failed for {source.name}: {e}")
                return []

        tag = root.tag.lower()

        # Sitemap index — recurse into child sitemaps
        if "sitemapindex" in tag:
            return await self._parse_sitemap_index(root, client, source, max_articles)

        # Direct urlset
        if "urlset" in tag:
            return self._parse_urlset(root, max_articles)

        logger.warning(f"[Sitemap] Unknown root tag for {source.name}: {root.tag}")
        return []

    async def _parse_sitemap_index(
        self, root, client: httpx.AsyncClient, source, max_articles: int
    ) -> list[str]:
        """
        Sitemap index: find child sitemaps, prefer news sitemaps, fetch most recent.
        """
        sitemap_urls = []
        for sitemap_el in root.findall("sm:sitemap", NS):
            loc = sitemap_el.findtext("sm:loc", namespaces=NS)
            if loc:
                sitemap_urls.append(loc.strip())

        # Prefer news sitemaps
        news_sitemaps = [u for u in sitemap_urls if "news" in u.lower()]
        target_sitemaps = news_sitemaps[:3] if news_sitemaps else sitemap_urls[:3]

        urls = []
        for sm_url in target_sitemaps:
            if len(urls) >= max_articles:
                break
            content = await self.get(sm_url, client)
            if not content:
                continue
            try:
                child_root = etree.fromstring(content.encode("utf-8"), parser=etree.XMLParser(recover=True))
                batch = self._parse_urlset(child_root, max_articles - len(urls))
                urls.extend(batch)
            except Exception as e:
                logger.warning(f"[Sitemap] Child sitemap parse error {sm_url}: {e}")

        logger.info(f"[Sitemap] {source.name} → {len(urls)} URLs from index")
        return urls[:max_articles]

    def _parse_urlset(self, root, max_articles: int) -> list[str]:
        """
        Parse <urlset> and return article URLs.
        Tries to filter recent articles using <lastmod> if available.
        """
        cutoff = datetime.utcnow() - timedelta(hours=RECENCY_HOURS)
        urls = []

        for url_el in root.findall("sm:url", NS):
            if len(urls) >= max_articles:
                break

            loc = url_el.findtext("sm:loc", namespaces=NS)
            if not loc:
                continue

            # Try date filtering
            lastmod = url_el.findtext("sm:lastmod", namespaces=NS)
            if lastmod:
                try:
                    from dateutil import parser as dp
                    dt = dp.parse(lastmod)
                    if dt.tzinfo:
                        from datetime import timezone
                        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    if dt < cutoff:
                        continue
                except Exception:
                    pass  # If date parse fails, include the URL anyway

            urls.append(loc.strip())

        return urls
