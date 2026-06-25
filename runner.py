"""
Runner — orchestrates a single scraper run.

Per run:
  1. Generate run_id
  2. Load active sources
  3. Process each source concurrently (bounded by semaphore)
  4. For each source: fetch URLs → dedup → scrape → process → save
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
from scrapers.article import ArticleScraper
from processors.category import CategoryProcessor
from processors.tag import TagProcessor
from processors.location import LocationProcessor
from utils.logger import get_logger

logger = get_logger("runner")

MAX_ARTICLES = int(os.getenv("MAX_ARTICLES_PER_SOURCE", "10"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_SOURCES", "10"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

# Shared processor instances (stateless, safe to reuse)
_category_proc = CategoryProcessor()
_tag_proc = TagProcessor()
_location_proc = LocationProcessor()

# Fetchers
_rss_fetcher = RSSFetcher(timeout=REQUEST_TIMEOUT)
_sitemap_fetcher = SitemapFetcher(timeout=REQUEST_TIMEOUT)
_html_fetcher = HTMLFetcher(timeout=REQUEST_TIMEOUT)
_article_scraper = ArticleScraper(timeout=REQUEST_TIMEOUT)


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
        logger.error(f"[Runner] {len(errors)} source(s) raised unhandled exceptions")

    logger.info(f"━━━ Scraper run complete | run_id={run_id} ━━━")


async def _process_source(source, client: httpx.AsyncClient, run_id: uuid.UUID, semaphore: asyncio.Semaphore):
    """
    Full pipeline for one source: fetch → dedup → scrape → process → save → log.
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

        logger.info(f"[Source] Starting: {source.name} ({source.language})")

        try:
            # ---------------------------------------------------------------
            # STEP 1: Fetch URLs (RSS → Sitemap → HTML)
            # ---------------------------------------------------------------
            urls, fetcher_used = await _fetch_urls(source, client)
            counters["urls_found"] = len(urls)

            if not urls:
                logger.warning(f"[Source] No URLs fetched from {source.name}")
                status = "failed"
                error_detail = "All fetchers returned 0 URLs"
            else:
                # ---------------------------------------------------------------
                # STEP 2-4: Per URL pipeline
                # ---------------------------------------------------------------
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
                    logger.info(f"[Source] {source.name}: all {dupes} URLs were duplicates")
                else:
                    status = "partial"

                logger.info(
                    f"[Source] {source.name} done | "
                    f"saved={saved} dupes={dupes} errors={errs} fetcher={fetcher_used}"
                )

        except Exception as e:
            logger.error(f"[Source] Unhandled error for {source.name}: {e}", exc_info=True)
            status = "failed"
            error_detail = str(e)

        # ---------------------------------------------------------------
        # STEP 5: Write run log
        # ---------------------------------------------------------------
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    log = await create_run_log(session, run_id, source.id, started_at)
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
            logger.error(f"[Source] Failed to write run log for {source.name}: {e}")


async def _fetch_urls(source, client: httpx.AsyncClient) -> tuple[list[str], Optional[str]]:
    """
    Try RSS → Sitemap → HTML in order. Return (urls, fetcher_name).
    """
    # RSS
    if source.rss_url:
        urls = await _rss_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        if urls:
            return urls, "rss"

    # Sitemap
    if source.sitemap_url:
        urls = await _sitemap_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        if urls:
            return urls, "sitemap"

    # HTML fallback
    urls = await _html_fetcher.fetch_urls(source, client, MAX_ARTICLES)
    if urls:
        return urls, "html"

    return [], None


async def _process_url(url: str, source, client: httpx.AsyncClient, counters: dict):
    """
    Full per-URL pipeline: dedup → scrape → process → save.
    Uses its own session per article to keep transactions tight.
    """
    try:
        # ---------------------------------------------------------------
        # DUPLICATE CHECK
        # ---------------------------------------------------------------
        async with AsyncSessionLocal() as session:
            if await url_exists(session, url):
                counters["duplicates_skipped"] += 1
                logger.debug(f"[Dedup] Skipping duplicate: {url}")
                return

        # ---------------------------------------------------------------
        # SCRAPE ARTICLE
        # ---------------------------------------------------------------
        config = source.html_scrape_config or {}
        article_data = await _article_scraper.scrape(url, client, config)

        if not article_data or not article_data.is_valid():
            logger.warning(f"[Article] No valid title found, skipping: {url}")
            counters["errors_skipped"] += 1
            return

        # ---------------------------------------------------------------
        # PROCESS: Category, Tag, Location
        # ---------------------------------------------------------------
        combined_text = " ".join(filter(None, [
            article_data.title,
            article_data.short_description,
            article_data.body,
        ]))

        async with AsyncSessionLocal() as session:
            async with session.begin():
                category_ids = await _category_proc.resolve(combined_text, session)
                tag_ids = await _tag_proc.resolve(combined_text, source.language.value, session)
                location_ids = await _location_proc.resolve(combined_text, source.language.value, session)

                # ---------------------------------------------------------------
                # SAVE ARTICLE
                # ---------------------------------------------------------------
                await save_article(
                    session=session,
                    source_id=source.id,
                    url=url,
                    title=article_data.title,
                    language=source.language.value,
                    short_description=article_data.short_description,
                    body=article_data.body,
                    image_url=article_data.image_url,
                    published_at=article_data.published_at,
                    category_ids=category_ids,
                    tag_ids=tag_ids,
                    location_ids=location_ids,
                )

        counters["articles_saved"] += 1
        title_preview = article_data.title[:60] + ("..." if len(article_data.title) > 60 else "")
        logger.debug(f"[Article] Saved: {title_preview}")

    except Exception as e:
        logger.error(f"[Article] Error processing {url}: {e}", exc_info=True)
        counters["errors_skipped"] += 1
