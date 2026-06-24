"""
Article scraper — given a URL, extracts:
  - title
  - short_description
  - body (plain text, optional)
  - image_url
  - published_at

Uses per-source CSS selectors from html_scrape_config if available,
otherwise falls back to common patterns and meta tags.
"""

import httpx
from bs4 import BeautifulSoup, Tag
from typing import Optional
from datetime import datetime

from fetchers.base import HEADERS
from scrapers.cleaner import ArticleCleaner
from utils.helpers import (
    parse_date, clean_text, extract_meta_description,
    extract_og_image, truncate
)
from utils.logger import get_logger

logger = get_logger("scraper.article")


class ArticleData:
    """Simple data container for scraped article fields."""
    def __init__(
        self,
        url: str,
        title: Optional[str] = None,
        short_description: Optional[str] = None,
        body: Optional[str] = None,
        image_url: Optional[str] = None,
        published_at: Optional[datetime] = None,
    ):
        self.url = url
        self.title = title
        self.short_description = short_description
        self.body = body
        self.image_url = image_url
        self.published_at = published_at

    def is_valid(self) -> bool:
        """Article is valid only if it has a title."""
        return bool(self.title and self.title.strip())


class ArticleScraper:

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.cleaner = ArticleCleaner()

    async def scrape(
        self,
        url: str,
        client: httpx.AsyncClient,
        config: Optional[dict] = None
    ) -> Optional[ArticleData]:
        """
        Fetch and extract article content from a URL.
        Returns ArticleData or None if title cannot be found.
        """
        try:
            response = await client.get(url, headers=HEADERS, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
            html = response.text
        except Exception as e:
            logger.warning(f"[Article] Fetch failed {url}: {e}")
            return None

        return self._parse(url, html, config or {})

    def _parse(self, url: str, html: str, config: dict) -> Optional[ArticleData]:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        data = ArticleData(url=url)

        # --- TITLE ---
        data.title = self._extract_title(soup, config)
        if not data.title:
            logger.debug(f"[Article] No title found: {url}")
            return None

        # --- SHORT DESCRIPTION ---
        data.short_description = self._extract_description(soup, html, config)

        # --- BODY ---
        data.body = self._extract_body(soup, config)

        # --- IMAGE ---
        data.image_url = self._extract_image(soup, html, config)

        # --- DATE ---
        data.published_at = self._extract_date(soup, config)

        return data

    # -----------------------------------------------------------------------
    # TITLE
    # -----------------------------------------------------------------------

    def _extract_title(self, soup: BeautifulSoup, config: dict) -> Optional[str]:
        # 1. Config selector
        if config.get("title"):
            el = soup.select_one(config["title"])
            if el:
                return clean_text(el.get_text())

        # 2. <h1>
        h1 = soup.find("h1")
        if h1:
            text = clean_text(h1.get_text())
            if text and len(text) > 5:
                return text

        # 3. og:title
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return clean_text(og_title["content"])

        # 4. <title> tag (strip site name)
        title_tag = soup.find("title")
        if title_tag:
            text = clean_text(title_tag.get_text())
            if text:
                # Remove " | SiteName" or " - SiteName" suffix
                for sep in [" | ", " - ", " – ", " — "]:
                    if sep in text:
                        text = text.split(sep)[0]
                return text.strip() or None

        return None

    # -----------------------------------------------------------------------
    # DESCRIPTION
    # -----------------------------------------------------------------------

    def _extract_description(self, soup: BeautifulSoup, html: str, config: dict) -> Optional[str]:
        # 1. Config selector
        if config.get("description"):
            el = soup.select_one(config["description"])
            if el:
                return truncate(clean_text(el.get_text()), 500)

        # 2. Meta description
        desc = extract_meta_description(html)
        if desc:
            return truncate(desc, 500)

        # 3. First substantial paragraph
        for p in soup.find_all("p"):
            text = clean_text(p.get_text())
            if text and len(text) > 60:
                return truncate(text, 500)

        return None

    # -----------------------------------------------------------------------
    # BODY
    # -----------------------------------------------------------------------

    def _extract_body(self, soup: BeautifulSoup, config: dict) -> Optional[str]:
        # 1. Config selector
        if config.get("body"):
            el = soup.select_one(config["body"])
            if el:
                return self.cleaner.clean(el)

        # 2. Common article body selectors (try in order)
        candidates = [
            "article",
            "[class*='article-body']",
            "[class*='article-content']",
            "[class*='story-body']",
            "[class*='post-content']",
            "[class*='news-body']",
            "[class*='entry-content']",
            "[itemprop='articleBody']",
            "main article",
            ".content",
        ]
        for selector in candidates:
            el = soup.select_one(selector)
            if el:
                text = self.cleaner.clean(el)
                if text and len(text) > 100:
                    return text

        return None

    # -----------------------------------------------------------------------
    # IMAGE
    # -----------------------------------------------------------------------

    def _extract_image(self, soup: BeautifulSoup, html: str, config: dict) -> Optional[str]:
        # 1. Config selector
        if config.get("image"):
            el = soup.select_one(config["image"])
            if el:
                return el.get("src") or el.get("data-src")

        # 2. og:image (most reliable)
        img = extract_og_image(html)
        if img:
            return img

        # 3. First image in article body
        for selector in ["article img", "[class*='article'] img", "figure img"]:
            el = soup.select_one(selector)
            if el:
                src = el.get("src") or el.get("data-src") or el.get("data-lazy-src")
                if src and not src.endswith(".gif") and "logo" not in src.lower():
                    return src

        return None

    # -----------------------------------------------------------------------
    # DATE
    # -----------------------------------------------------------------------

    def _extract_date(self, soup: BeautifulSoup, config: dict) -> Optional[datetime]:
        # 1. Config selector
        if config.get("date"):
            el = soup.select_one(config["date"])
            if el:
                date_str = el.get("datetime") or el.get("content") or el.get_text()
                result = parse_date(date_str)
                if result:
                    return result

        # 2. <time datetime="...">
        time_el = soup.find("time")
        if time_el:
            dt = time_el.get("datetime") or time_el.get_text()
            result = parse_date(dt)
            if result:
                return result

        # 3. JSON-LD structured data
        import json, re
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = data[0]
                for key in ["datePublished", "dateCreated", "dateModified"]:
                    if key in data:
                        result = parse_date(data[key])
                        if result:
                            return result
            except Exception:
                pass

        # 4. Meta tags
        for attr, value in [
            ("property", "article:published_time"),
            ("name", "publish-date"),
            ("name", "date"),
            ("name", "DC.date"),
            ("itemprop", "datePublished"),
        ]:
            meta = soup.find("meta", {attr: value})
            if meta and meta.get("content"):
                result = parse_date(meta["content"])
                if result:
                    return result

        return None
