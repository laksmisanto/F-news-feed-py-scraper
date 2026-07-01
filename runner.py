"""
Runner — orchestrates a single scraper run.

Pipeline:
  1. Generate run_id
  2. Load active sources
  3. Decide if Playwright is needed (any source.requires_browser or auto_escalate)
  4. Launch shared Playwright browser if needed
  5. Process each source concurrently (bounded by semaphore)
  6. For each source:
        a) Discovery:
             - source.requires_browser → HeadlessFetcher (Playwright)
             - else                    → RSS / Sitemap / HTML / Crawler chain (httpx)
        b) For each URL:
             - dedup vs DB
             - render with Playwright (if requires_browser) or fetch with httpx
             - extract via extraction chain
             - auto-escalate to Playwright if body too short / 403 (opt-in)
             - process categories/tags/locations
             - save

Configuration via .env:
  MIN_BODY_LEN              (default 200)  → bodies shorter trigger escalation
  ESCALATE_ON_403           (default true) → 403 responses trigger escalation
  MAX_PLAYWRIGHT_PER_RUN    (default 50)   → safety cap on Playwright renders
  PLAYWRIGHT_TIMEOUT_MS     (default 30000)

Per-source opt-in for auto-escalation goes in crawl_config:
  {"auto_escalate": true, "playwright_wait": "networkidle"}
"""

import asyncio
import os
import uuid
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
from fetchers.headless import HeadlessFetcher
from fetchers.base import HEADERS
from scrapers.article import extract_article
from processors.category import process_categories
from processors.tag import TagProcessor
from processors.location import LocationProcessor
from utils.browser import PlaywrightManager
from utils.logger import get_logger

logger = get_logger("runner")

MAX_ARTICLES = int(os.getenv("MAX_ARTICLES_PER_SOURCE", "10"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_SOURCES", "10"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

# Playwright-specific tunables
MIN_BODY_LEN = int(os.getenv("MIN_BODY_LEN", "200"))
ESCALATE_ON_403 = os.getenv("ESCALATE_ON_403", "true").lower() == "true"
MAX_PLAYWRIGHT_PER_RUN = int(os.getenv("MAX_PLAYWRIGHT_PER_RUN", "50"))
PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))

# Shared stateless instances
_tag_proc = TagProcessor()
_location_proc = LocationProcessor()

_rss_fetcher = RSSFetcher(timeout=REQUEST_TIMEOUT)
_sitemap_fetcher = SitemapFetcher(timeout=REQUEST_TIMEOUT)
_html_fetcher = HTMLFetcher(timeout=REQUEST_TIMEOUT)
_crawler_fetcher = CrawlerFetcher(timeout=REQUEST_TIMEOUT)
_headless_fetcher = HeadlessFetcher(timeout=REQUEST_TIMEOUT)


# ===========================================================================
# Run-level state
# ===========================================================================

class RunBudget:
    """Tracks per-run Playwright usage against the cap."""
    def __init__(self, cap: int):
        self.cap = cap
        self.used = 0
        self._lock = asyncio.Lock()

    async def take(self) -> bool:
        async with self._lock:
            if self.used >= self.cap:
                return False
            self.used += 1
            return True


# ===========================================================================
# Entry point
# ===========================================================================

async def run_scraper():
    run_id = uuid.uuid4()
    logger.info(f"━━━ Scraper run started | run_id={run_id} ━━━")

    async with AsyncSessionLocal() as session:
        sources = await get_active_sources(session)

    if not sources:
        logger.warning("[Runner] No active sources found.")
        return

    logger.info(f"[Runner] {len(sources)} active sources to process")

    # Decide if we need Playwright at all this run
    needs_browser = any(getattr(s, "requires_browser", False) for s in sources)
    needs_browser = needs_browser or any(
        (s.crawl_config or {}).get("auto_escalate", False) for s in sources
    )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    budget = RunBudget(MAX_PLAYWRIGHT_PER_RUN)

    async with httpx.AsyncClient() as client:
        if needs_browser:
            logger.info(
                f"[Runner] Playwright enabled this run "
                f"(cap={MAX_PLAYWRIGHT_PER_RUN} renders)"
            )
            try:
                async with PlaywrightManager() as pw:
                    await _process_all(sources, client, pw, run_id, semaphore, budget)
            except Exception as e:
                logger.error(
                    f"[Runner] Playwright init failed: {e}. "
                    f"Falling back to httpx-only for this run.",
                    exc_info=True,
                )
                await _process_all(sources, client, None, run_id, semaphore, budget)
        else:
            await _process_all(sources, client, None, run_id, semaphore, budget)

    logger.info(
        f"━━━ Scraper run complete | run_id={run_id} "
        f"| playwright_used={budget.used}/{budget.cap} ━━━"
    )


async def _process_all(sources, client, pw, run_id, semaphore, budget):
    tasks = [
        _process_source(source, client, pw, run_id, semaphore, budget)
        for source in sources
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        logger.error(f"[Runner] {len(errors)} source(s) raised unhandled exceptions")


# ===========================================================================
# Per-source processing
# ===========================================================================

async def _process_source(
    source,
    client: httpx.AsyncClient,
    pw: Optional[PlaywrightManager],
    run_id: uuid.UUID,
    semaphore: asyncio.Semaphore,
    budget: RunBudget,
):
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

        requires_browser = getattr(source, "requires_browser", False)
        mode = "PLAYWRIGHT" if requires_browser else "httpx"
        logger.info(f"[Source] Starting: {source.name} ({source.language.value}) [{mode}]")

        try:
            # ──────────────────────────────────────────────────────────────
            # STEP 1: Discovery — pick the right fetcher
            # ──────────────────────────────────────────────────────────────
            if requires_browser:
                if pw is None:
                    raise RuntimeError(
                        f"{source.name} requires_browser=True but Playwright "
                        f"is unavailable this run"
                    )
                urls, fetcher_used = await _discover_with_playwright(source, pw)
            else:
                urls, fetcher_used = await _discover_with_httpx(source, client)

            counters["urls_found"] = len(urls)

            if not urls:
                logger.warning(f"[Source] No URLs fetched from {source.name}")
                status = "failed"
                error_detail = "All fetchers returned 0 URLs"
            else:
                # ──────────────────────────────────────────────────────────
                # STEP 2: Per-URL processing
                # ──────────────────────────────────────────────────────────
                for url in urls:
                    await _process_url(
                        url=url,
                        source=source,
                        client=client,
                        pw=pw,
                        budget=budget,
                        counters=counters,
                    )

                saved = counters["articles_saved"]
                dupes = counters["duplicates_skipped"]
                errs = counters["errors_skipped"]

                if saved > 0:
                    status = "success"
                elif dupes == len(urls):
                    status = "success"
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

        # ────────────────────────────────────────────────────────────────
        # STEP 3: Write run log
        # ────────────────────────────────────────────────────────────────
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


# ===========================================================================
# Discovery
# ===========================================================================

async def _discover_with_httpx(
    source, client: httpx.AsyncClient
) -> tuple[list[str], Optional[str]]:
    """
    Standard chain: RSS → Sitemap → HTML, plus Crawler (supplemental).
    """
    primary_urls: list[str] = []
    primary_fetcher: Optional[str] = None

    if source.rss_url:
        primary_urls = await _rss_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        if primary_urls:
            primary_fetcher = "rss"

    if not primary_urls and source.sitemap_url:
        primary_urls = await _sitemap_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        if primary_urls:
            primary_fetcher = "sitemap"

    if not primary_urls:
        primary_urls = await _html_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        if primary_urls:
            primary_fetcher = "html"

    crawler_urls: list[str] = []
    if getattr(source, "crawl_enabled", False):
        try:
            crawler_urls = await _crawler_fetcher.fetch_urls(source, client, MAX_ARTICLES)
        except Exception as e:
            logger.warning(f"[Runner] Crawler error for {source.name}: {e}")

    # Merge + dedupe
    seen = set()
    merged: list[str] = []
    for u in primary_urls + crawler_urls:
        if u and u not in seen:
            seen.add(u)
            merged.append(u)
    merged = merged[:MAX_ARTICLES]

    if primary_fetcher:
        fetcher_used = primary_fetcher
    elif crawler_urls:
        fetcher_used = "crawler"
    else:
        fetcher_used = None

    return merged, fetcher_used


async def _discover_with_playwright(
    source, pw: PlaywrightManager
) -> tuple[list[str], Optional[str]]:
    """Use Playwright to render seed pages and extract article links."""
    cfg = source.crawl_config or {}
    locale = "bn-BD" if source.language.value == "bn" else "en-US"

    async with pw.context(locale=locale) as ctx:
        urls = await _headless_fetcher.fetch_urls_with_context(
            source, ctx, max_articles=MAX_ARTICLES
        )

    return urls, ("headless" if urls else None)


# ===========================================================================
# Per-URL processing
# ===========================================================================

async def _process_url(
    url: str,
    source,
    client: httpx.AsyncClient,
    pw: Optional[PlaywrightManager],
    budget: RunBudget,
    counters: dict,
):
    try:
        # ──────────────────────────────────────────────────────────────
        # 1. DUPLICATE CHECK
        # ──────────────────────────────────────────────────────────────
        async with AsyncSessionLocal() as session:
            if await url_exists(session, url):
                counters["duplicates_skipped"] += 1
                return

        requires_browser = getattr(source, "requires_browser", False)
        cfg = source.crawl_config or {}
        auto_escalate = bool(cfg.get("auto_escalate", False))
        playwright_wait = cfg.get("playwright_wait", "domcontentloaded")
        css_config = source.html_scrape_config or None

        # ──────────────────────────────────────────────────────────────
        # 2. FETCH HTML — Playwright (forced) or httpx (default)
        # ──────────────────────────────────────────────────────────────
        html: Optional[str] = None
        used_playwright = False
        http_status: Optional[int] = None

        if requires_browser:
            if pw is None:
                counters["errors_skipped"] += 1
                logger.warning(f"[Article] {url}: requires_browser but pw unavailable")
                return
            if not await budget.take():
                counters["errors_skipped"] += 1
                logger.warning(f"[Article] {url}: Playwright budget exhausted")
                return
            async with pw.context() as ctx:
                html = await ctx.render(
                    url, wait_until=playwright_wait, timeout=PLAYWRIGHT_TIMEOUT_MS
                )
            used_playwright = True
        else:
            try:
                response = await client.get(
                    url,
                    headers=HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=True,
                )
                http_status = response.status_code
                if response.status_code < 400:
                    html = response.text
            except Exception as e:
                logger.debug(f"[Article] httpx error {url}: {e}")
                http_status = None

        # ──────────────────────────────────────────────────────────────
        # 3. EXTRACT
        # ──────────────────────────────────────────────────────────────
        article_data = None
        if html:
            article_data = await extract_article(
                url=url, html=html, css_config=css_config
            )

        # ──────────────────────────────────────────────────────────────
        # 4. AUTO-ESCALATE TO PLAYWRIGHT (opt-in, non-requires_browser only)
        # ──────────────────────────────────────────────────────────────
        if not used_playwright and auto_escalate and pw is not None:
            should_escalate = _should_escalate(article_data, http_status)
            if should_escalate and await budget.take():
                logger.info(f"[Article] Escalating to Playwright: {url}")
                async with pw.context() as ctx:
                    html_pw = await ctx.render(
                        url, wait_until=playwright_wait, timeout=PLAYWRIGHT_TIMEOUT_MS
                    )
                if html_pw:
                    article_data_pw = await extract_article(
                        url=url, html=html_pw, css_config=css_config
                    )
                    if article_data_pw and _is_better(article_data_pw, article_data):
                        article_data = article_data_pw
                        used_playwright = True

        # ──────────────────────────────────────────────────────────────
        # 5. QUALITY GATE
        # ──────────────────────────────────────────────────────────────
        if not article_data or not article_data.get("title"):
            counters["errors_skipped"] += 1
            return

        # ──────────────────────────────────────────────────────────────
        # 6. PROCESS + SAVE
        # ──────────────────────────────────────────────────────────────
        combined_text = " ".join(filter(None, [
            article_data.get("title"),
            article_data.get("short_description"),
            article_data.get("body"),
        ]))

        async with AsyncSessionLocal() as session:
            async with session.begin():
                category_objs = await process_categories(
                    session=session,
                    article=None,
                    url=url,
                    text=combined_text,
                    section=article_data.get("section"),
                )
                category_ids = [c.id for c in category_objs] if category_objs else []
                tag_ids = await _tag_proc.resolve(
                    combined_text, source.language.value, session
                )
                location_ids = await _location_proc.resolve(
                    combined_text, source.language.value, session
                )

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
        renderer = "pw" if used_playwright else "httpx"
        extractors = article_data.get("extractors_used", "?")
        logger.debug(f"[Article] Saved ({renderer}/{extractors}): {title_preview}")

    except Exception as e:
        logger.error(f"[Article] Error processing {url}: {e}", exc_info=True)
        counters["errors_skipped"] += 1


# ===========================================================================
# Escalation heuristics
# ===========================================================================

def _should_escalate(article_data: Optional[dict], http_status: Optional[int]) -> bool:
    """
    Decide if a Playwright retry is worthwhile.
    Returns True if:
      - HTTP 403 (and ESCALATE_ON_403 is set), OR
      - Extracted body is shorter than MIN_BODY_LEN, OR
      - Extraction failed entirely (no title)
    """
    if ESCALATE_ON_403 and http_status == 403:
        return True
    if article_data is None:
        return True
    if not article_data.get("title"):
        return True
    body = article_data.get("body") or ""
    if len(body) < MIN_BODY_LEN:
        return True
    return False


def _is_better(new: dict, old: Optional[dict]) -> bool:
    """Decide if a re-extracted article is meaningfully better than the prior."""
    if old is None:
        return True
    new_body = len(new.get("body") or "")
    old_body = len(old.get("body") or "")
    # Prefer the version with the longer body
    if new_body > old_body * 1.2:
        return True
    # Or that filled in a missing image
    if not old.get("image_url") and new.get("image_url"):
        return True
    return False
