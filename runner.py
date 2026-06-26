"""
Runner — orchestrates a single scraper run.

Per run:
  1. Generate run_id
  2. Load active sources
  3. Process each source concurrently (bounded by semaphore)
  4. For each source:
        a) Fetch URLs via the fetcher chain:
           RSS → Sitemap → HTML  (primary chain)
           + Crawler              (supplemental, when source.crawl_enabled=True)
        b) Merge & deduplicate URLs
        c) For each URL → dedup vs DB → extract article → process → save

Bugs fixed in this version:
  - Removed import of ArticleScraper / ArticleData (no longer exist)
  - Removed import of CategoryProcessor (replaced by process_categories function)
  - Adapted to new dict-returning extract_article()
  - Adapted to new process_categories(session, article, url, text, section) signature

New in this version:
  - CrawlerFetcher integration. When source.crawl_enabled = True the crawler
    runs alongside the primary fetcher chain and its URLs are merged in.
  - fetcher_used reporting tracks both primary fetcher and crawler contribution.
"""

import asyncio
import uuid
import os
from datetime import datetime
from typing import Optional

import httpx

from db.session import AsyncSessionLocal
from db.queries import (
    get_active_sources, url_exists, save_article,
    create_run_log, finalize_run_log
)
from fetchers.rss import RSSFetcher
from fetchers.sitemap import SitemapFetcher
from fetchers.html import HTMLFetcher
from fetchers.crawler import CrawlerFetcher
from fetchers.base import HEADERS
from scrapers.article import extract_article
from processors.category import process_categories
from processors.tag import TagProcessor
from processors.location import LocationProcessor
from utils.logger import get_logger

logger = get_logger("runner")

MAX_ARTICLES = int(os.getenv("MAX_ARTICLES_PER_SOURCE", "10"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_SOURCES", "10"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

# Shared processor instances (stateless, safe to reuse across tasks)
_tag_proc = TagProcessor()
_location_proc = LocationProcessor()

# Fetchers (stateless wrappers around httpx — share one instance)
_rss_fetcher = RSSFetcher(timeout=REQUEST_TIMEOUT)
_sitemap_fetcher = SitemapFetcher(timeout=REQUEST_TIMEOUT)
_html_fetcher = HTMLFetcher(timeout=REQUEST_TIMEOUT)
_crawler_fetcher = CrawlerFetcher(timeout=REQUEST_TIMEOUT)


async def run_scraper():
    """
    Entry point called by the scheduler every N minutes.
    """
    run_id = uuid.uuid4()
    logger.info(f"━━━ Scraper run started | run_id={run_id} ━━━")

    async with AsyncSessionLocal() as session:
        sources = await get_active_sources(session)

    if not sources:
        logger.warning("[Runner] No active sources found.")
        return

    logger.info(f"[Runner] {len(sources)} active sources to process")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient() as client:
        tasks = [
            _process_source(source, client, run_id, semaphore)
            for source in sources
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        logger.error(
            f"[Runner] {len(errors)} source(s) raised unhandled exceptions"
        )

    logger.info(f"━━━ Scraper run complete | run_id={run_id} ━━━")


async def _process_source(
    source,
    client: httpx.AsyncClient,
    run_id: uuid.UUID,
    semaphore: asyncio.Semaphore,
):
    """
    Full pipeline for one source: fetch → dedup → extract → process → save → log.
    """
    async with semaphore:
        started_at = datetime.utcnow()
        counters = {
            "urls_found": 0,
            "articles_saved": 0,
            "duplicates_skipped": 0,
            "errors_skipped": 0,
        }
        fetcher_used = None
        error_detail = None
        status = "failed"

        logger.info(f"[Source] Starting: {source.name} ({source.language.value})")

        try:
            # ──────────────────────────────────────────────────────────────
            # STEP 1: Fetch URLs (RSS → Sitemap → HTML, + Crawler if enabled)
            # ──────────────────────────────────────────────────────────────
            urls, fetcher_used = await _fetch_urls(source, client)
            counters["urls_found"] = len(urls)

            if not urls:
                logger.warning(f"[Source] No URLs fetched from {source.name}")
                status = "failed"
                error_detail = "All fetchers returned 0 URLs"
            else:
                # ──────────────────────────────────────────────────────────
                # STEP 2-4: Per URL pipeline
                # ──────────────────────────────────────────────────────────
                for url in urls:
                    await _process_url(
                        url=url,
                        source=source,
                        client=client,
                        counters=counters,
                    )

                saved = counters["articles_saved"]
                dupes = counters["duplicates_skipped"]
                errs = counters["errors_skipped"]

                if saved > 0:
                    status = "success"
                elif dupes == len(urls):
                    status = "success"  # All dupes = clean run
                    logger.info(
                        f"[Source] {source.name}: all {dupes} URLs were duplicates"
                    )
                else:
                    status = "partial"

                logger.info(
                    f"[Source] {source.name} done | "
                    f"saved={saved} dupes={dupes} errors={errs} fetcher={fetcher_used}"
                )

        except Exception as e:
            logger.error(
                f"[Source] Unhandled error for {source.name}: {e}",
                exc_info=True,
            )
            status = "failed"
            error_detail = str(e)

        # ────────────────────────────────────────────────────────────────
        # STEP 5: Write run log
        # ────────────────────────────────────────────────────────────────
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    log = await create_run_log(
                        session, run_id, source.id, started_at
                    )
                    await finalize_run_log(
                        session=session,
                        log=log,
                        status=status,
                        fetcher_used=fetcher_used,
                        urls_found=counters["urls_found"],
                        articles_saved=counters["articles_saved"],
                        duplicates_skipped=counters["duplicates_skipped"],
                        errors_skipped=counters["errors_skipped"],
                        error_detail=error_detail,
                    )
        except Exception as e:
            logger.error(
                f"[Source] Failed to write run log for {source.name}: {e}"
            )


async def _fetch_urls(
    source, client: httpx.AsyncClient
) -> tuple[list[str], Optional[str]]:
    """
    Build the URL list for a source.

    Logic:
      1. Run the primary chain in order: RSS → Sitemap → HTML.
         First fetcher that returns URLs wins as primary.
      2. If source.crawl_enabled is True, ALSO run the crawler.
      3. Merge URLs preserving order, deduped.

    The fetcher_used field reflects the primary discovery method:
      - 'rss' / 'sitemap' / 'html' if those were the primary
      - 'crawler' if the crawler was the only one to return URLs
    """
    primary_urls: list[str] = []
    primary_fetcher: Optional[str] = None

    # RSS
    if source.rss_url:
        primary_urls = await _rss_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        if primary_urls:
            primary_fetcher = "rss"

    # Sitemap (only if RSS gave nothing)
    if not primary_urls and source.sitemap_url:
        primary_urls = await _sitemap_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        if primary_urls:
            primary_fetcher = "sitemap"

    # HTML last-resort listing scrape
    if not primary_urls:
        primary_urls = await _html_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        if primary_urls:
            primary_fetcher = "html"

    # Crawler (supplemental when enabled)
    crawler_urls: list[str] = []
    if getattr(source, "crawl_enabled", False):
        try:
            crawler_urls = await _crawler_fetcher.fetch_urls(
                source, client, MAX_ARTICLES
            )
        except Exception as e:
            logger.warning(f"[Runner] Crawler error for {source.name}: {e}")

    # Merge + dedupe, preserving order: primary first, then new from crawler
    seen = set()
    merged: list[str] = []
    for u in primary_urls + crawler_urls:
        if u and u not in seen:
            seen.add(u)
            merged.append(u)

    # Cap total to MAX_ARTICLES
    merged = merged[:MAX_ARTICLES]

    # Decide fetcher_used label for the run log
    if primary_fetcher:
        fetcher_used = primary_fetcher
    elif crawler_urls:
        fetcher_used = "crawler"
    else:
        fetcher_used = None

    return merged, fetcher_used


async def _process_url(
    url: str,
    source,
    client: httpx.AsyncClient,
    counters: dict,
):
    """
    Full per-URL pipeline:
      1. Dedup check against DB
      2. Fetch HTML
      3. Run extraction chain
      4. Resolve categories, tags, locations
      5. Save article + M2M rows
    """
    try:
        # ──────────────────────────────────────────────────────────────
        # 1. DUPLICATE CHECK
        # ──────────────────────────────────────────────────────────────
        async with AsyncSessionLocal() as session:
            if await url_exists(session, url):
                counters["duplicates_skipped"] += 1
                logger.debug(f"[Dedup] Skipping duplicate: {url}")
                return

        # ──────────────────────────────────────────────────────────────
        # 2. FETCH HTML using the shared httpx client (efficient pooling)
        # ──────────────────────────────────────────────────────────────
        try:
            response = await client.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            response.raise_for_status()
            html = response.text
        except Exception as e:
            counters["errors_skipped"] += 1
            logger.debug(f"[Article] Failed to fetch HTML {url}: {e}")
            return

        if not html:
            counters["errors_skipped"] += 1
            return

        # ──────────────────────────────────────────────────────────────
        # 3. EXTRACT ARTICLE (returns dict or None)
        # ──────────────────────────────────────────────────────────────
        css_config = source.html_scrape_config or None
        article_data = await extract_article(
            url=url,
            html=html,
            css_config=css_config,
        )

        if not article_data or not article_data.get("title"):
            counters["errors_skipped"] += 1
            logger.warning(f"[Article] No valid title, skipping: {url}")
            return

        # ──────────────────────────────────────────────────────────────
        # 4. PROCESS: Category, Tag, Location
        # ──────────────────────────────────────────────────────────────
        combined_text = " ".join(filter(None, [
            article_data.get("title"),
            article_data.get("short_description"),
            article_data.get("body"),
        ]))

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Categories: function returns Category ORM objects
                category_objs = await process_categories(
                    session=session,
                    article=None,
                    url=url,
                    text=combined_text,
                    section=article_data.get("section"),
                )
                category_ids = [c.id for c in category_objs] if category_objs else []

                # Tags (still class-based)
                tag_ids = await _tag_proc.resolve(
                    combined_text, source.language.value, session
                )

                # Locations (still class-based)
                location_ids = await _location_proc.resolve(
                    combined_text, source.language.value, session
                )

                # ──────────────────────────────────────────────────────
                # 5. SAVE ARTICLE
                # ──────────────────────────────────────────────────────
                await save_article(
                    session=session,
                    source_id=source.id,
                    url=url,
                    title=article_data["title"],
                    language=source.language.value,
                    short_description=article_data.get("short_description"),
                    body=article_data.get("body"),
                    image_url=article_data.get("image_url"),
                    published_at=article_data.get("published_at"),
                    category_ids=category_ids,
                    tag_ids=tag_ids,
                    location_ids=location_ids,
                )

        counters["articles_saved"] += 1
        title_preview = article_data["title"][:60] + (
            "..." if len(article_data["title"]) > 60 else ""
        )
        extractors = article_data.get("extractors_used", "?")
        logger.debug(f"[Article] Saved ({extractors}): {title_preview}")

    except Exception as e:
        logger.error(f"[Article] Error processing {url}: {e}", exc_info=True)
        counters["errors_skipped"] += 1
