"""
Seed script — populates the database with:

  1. 23 confirmed BD + International news sources with correct RSS URLs
  2. 4 TV portals using sitemap fallback (Phase 2)
  3. BD location hierarchy from locations_bd.json
  4. International countries from countries.json

All sources have html_scrape_config = None. The new article extractor
(scrapers/article.py) handles them generically via JSON-LD, OpenGraph,
Trafilatura, Newspaper4k, and readability fallback chain.

Run: python seed.py
"""

import asyncio
import json
import os
from datetime import datetime, timezone

from db.session import AsyncSessionLocal, engine
from db.models import Base, Source, Location, LanguageEnum
from sqlalchemy import select

from utils.logger import get_logger

logger = get_logger("seed")

BD_GEO_PATH = os.path.join("config", "keywords", "locations_bd.json")
COUNTRIES_PATH = os.path.join("config", "keywords", "countries.json")


# ===========================================================================
# SOURCES — 27 confirmed working sources
# ===========================================================================
# Notes:
#   - All html_scrape_config = None → generic extractor chain handles them.
#   - Only set html_scrape_config on a specific source IF you confirm the
#     generic extractor fails repeatedly on that source.
#   - RSS URLs verified June 2026. If a feed breaks, update the rss_url
#     and re-run seed.py.
# ===========================================================================

SOURCES = [
    # ──────────────────────────────────────────────────────────────────────
    # TIER 1 — BANGLA SOURCES (RSS verified)
    # ──────────────────────────────────────────────────────────────────────
    {
        "name": "Prothom Alo",
        "language": "bn",
        "base_url": "https://www.prothomalo.com",
        "rss_url": "https://www.prothomalo.com/feed/",
        "sitemap_url": "https://www.prothomalo.com/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "priority": 1,
    },
    {
        "name": "Kaler Kantho",
        "language": "bn",
        "base_url": "https://www.kalerkantho.com",
        "rss_url": "https://www.kalerkantho.com/rss.xml",
        "sitemap_url": "https://www.kalerkantho.com/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "priority": 2,
    },
    {
        "name": "Jugantor",
        "language": "bn",
        "base_url": "https://www.jugantor.com",
        "rss_url": "https://www.jugantor.com/feed/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 3,
    },
    {
        "name": "Samakal",
        "language": "bn",
        "base_url": "https://samakal.com",
        "rss_url": "https://samakal.com/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 4,
    },
    {
        "name": "Ittefaq",
        "language": "bn",
        "base_url": "https://www.ittefaq.com.bd",
        "rss_url": "https://www.ittefaq.com.bd/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 5,
    },
    {
        "name": "Bangladesh Pratidin",
        "language": "bn",
        "base_url": "https://www.bd-pratidin.com",
        "rss_url": "https://www.bd-pratidin.com/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 6,
    },
    {
        "name": "bdnews24",
        "language": "bn",
        "base_url": "https://bdnews24.com",
        "rss_url": "https://bdnews24.com/?widgetName=rssfeed&widgetId=1150&getXmlFeed=true",
        "sitemap_url": "https://bdnews24.com/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "priority": 7,
    },
    {
        "name": "BanglaNews24",
        "language": "bn",
        "base_url": "https://www.banglanews24.com",
        "rss_url": "https://www.banglanews24.com/rss/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 8,
    },
    {
        "name": "JagoNews24",
        "language": "bn",
        "base_url": "https://www.jagonews24.com",
        "rss_url": "https://www.jagonews24.com/rss/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 9,
    },
    {
        "name": "Dhaka Post",
        "language": "bn",
        "base_url": "https://www.dhakapost.com",
        "rss_url": "https://www.dhakapost.com/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 10,
    },
    {
        "name": "BBC Bangla",
        "language": "bn",
        "base_url": "https://www.bbc.com/bengali",
        "rss_url": "https://feeds.bbci.co.uk/bengali/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 11,
    },
    {
        "name": "DW Bangla",
        "language": "bn",
        "base_url": "https://www.dw.com/bn",
        "rss_url": "https://rss.dw.com/rdf/rss-ben-all",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 12,
    },
    {
        "name": "VOA Bangla",
        "language": "bn",
        "base_url": "https://www.voabangla.com",
        "rss_url": "https://www.voabangla.com/api/zmoqiyrppv",
        "sitemap_url": "https://www.voabangla.com/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "priority": 13,
    },

    # ──────────────────────────────────────────────────────────────────────
    # TIER 1 — ENGLISH SOURCES (BD + International, RSS verified)
    # ──────────────────────────────────────────────────────────────────────
    {
        "name": "The Daily Star",
        "language": "en",
        "base_url": "https://www.thedailystar.net",
        "rss_url": "https://www.thedailystar.net/frontpage/rss.xml",
        "sitemap_url": "https://www.thedailystar.net/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "priority": 14,
    },
    {
        "name": "TBS News",
        "language": "en",
        "base_url": "https://www.tbsnews.net",
        "rss_url": "https://www.tbsnews.net/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 15,
    },
    {
        "name": "Dhaka Tribune",
        "language": "en",
        "base_url": "https://www.dhakatribune.com",
        "rss_url": "https://www.dhakatribune.com/feed",
        "sitemap_url": "https://www.dhakatribune.com/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "priority": 16,
    },
    {
        "name": "New Age",
        "language": "en",
        "base_url": "https://www.newagebd.net",
        "rss_url": "https://www.newagebd.net/rss",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 17,
    },
    {
        "name": "The Financial Express BD",
        "language": "en",
        "base_url": "https://thefinancialexpress.com.bd",
        "rss_url": "https://thefinancialexpress.com.bd/rss",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 18,
    },
    {
        "name": "Al Jazeera",
        "language": "en",
        "base_url": "https://www.aljazeera.com",
        "rss_url": "https://www.aljazeera.com/xml/rss/all.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 19,
    },
    {
        "name": "AP News",
        "language": "en",
        "base_url": "https://apnews.com",
        "rss_url": None,
        "sitemap_url": "https://apnews.com/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "priority": 20,
    },
    {
        "name": "The Guardian",
        "language": "en",
        "base_url": "https://www.theguardian.com",
        "rss_url": "https://www.theguardian.com/world/rss",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 21,
    },
    {
        "name": "NDTV",
        "language": "en",
        "base_url": "https://www.ndtv.com",
        "rss_url": "https://feeds.feedburner.com/ndtvnews-top-stories",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 22,
    },
    {
        "name": "The Hindu",
        "language": "en",
        "base_url": "https://www.thehindu.com",
        "rss_url": "https://www.thehindu.com/news/international/feeder/default.rss",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 23,
    },

    # ──────────────────────────────────────────────────────────────────────
    # TIER 2 — TV PORTALS (JS-rendered SPAs, require Playwright)
    # ──────────────────────────────────────────────────────────────────────
    {
        "name": "Somoy News TV",
        "language": "bn",
        "base_url": "https://www.somoynews.tv",
        "rss_url": None,
        "sitemap_url": "https://www.somoynews.tv/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "requires_browser": True,
        "crawl_config": {
            "seed_paths": ["/"],
            "playwright_wait": "networkidle",
            "max_pages_per_run": 4,
            "rate_limit_seconds": 2.0,
            "exclude_patterns": ["/tag/", "/video/", "/photo/", "/categories/"],
        },
        "priority": 24,
    },
    {
        "name": "Jamuna TV",
        "language": "bn",
        "base_url": "https://www.jamuna.tv",
        "rss_url": None,
        "sitemap_url": "https://www.jamuna.tv/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "requires_browser": True,
        "crawl_config": {
            "seed_paths": ["/", "/national", "/international", "/politics", "/sports"],
            "playwright_wait": "domcontentloaded",
            "max_pages_per_run": 8,
            "rate_limit_seconds": 1.5,
            "article_url_patterns": ["/[^/]+/\\d{4,}$"],
            "exclude_patterns": ["/tag/", "/video/", "/program/", "/category/"],
        },
        "priority": 25,
    },
    {
        "name": "Ekattor TV",
        "language": "bn",
        "base_url": "https://ekattor.tv",
        "rss_url": None,
        "sitemap_url": "https://ekattor.tv/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "requires_browser": True,
        "crawl_config": {
            "seed_paths": ["/", "/national", "/international", "/politics", "/sports"],
            "playwright_wait": "domcontentloaded",
            "max_pages_per_run": 8,
            "rate_limit_seconds": 1.5,
            "exclude_patterns": ["/tag/", "/video/", "/program/", "/category/"],
        },
        "priority": 26,
    },
    {
        "name": "DBC News",
        "language": "bn",
        "base_url": "https://www.dbcnews.tv",
        "rss_url": None,
        "sitemap_url": "https://www.dbcnews.tv/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "requires_browser": True,
        "crawl_config": {
            "seed_paths": ["/", "/national", "/international", "/politics", "/sports"],
            "playwright_wait": "domcontentloaded",
            "max_pages_per_run": 8,
            "rate_limit_seconds": 1.5,
            "exclude_patterns": ["/tag/", "/video/", "/program/", "/category/"],
        },
        "priority": 27,
    },
]


# ===========================================================================
# Seed functions
# ===========================================================================

async def seed_sources(session):
    """Upsert sources by base_url. Updates existing rows with new RSS URLs."""
    added = 0
    updated = 0
    for s in SOURCES:
        result = await session.execute(
            select(Source).where(Source.base_url == s["base_url"])
        )
        existing = result.scalar_one_or_none()

        if existing:
            changed = False
            if existing.rss_url != s.get("rss_url"):
                existing.rss_url = s.get("rss_url")
                changed = True
            if existing.sitemap_url != s.get("sitemap_url"):
                existing.sitemap_url = s.get("sitemap_url")
                changed = True
            if existing.html_scrape_config != s.get("html_scrape_config"):
                existing.html_scrape_config = s.get("html_scrape_config")
                changed = True
            if existing.is_active != s.get("is_active", True):
                existing.is_active = s.get("is_active", True)
                changed = True
            if existing.requires_browser != s.get("requires_browser", False):
                existing.requires_browser = s.get("requires_browser", False)
                changed = True
            if existing.crawl_config != s.get("crawl_config"):
                existing.crawl_config = s.get("crawl_config")
                changed = True
            if changed:
                existing.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                updated += 1
                logger.info(f"[Seed] Updated source: {s['name']}")
            else:
                logger.info(f"[Seed] Source already current: {s['name']}")
            continue

        source = Source(
            name=s["name"],
            language=LanguageEnum(s["language"]),
            base_url=s["base_url"],
            rss_url=s.get("rss_url"),
            sitemap_url=s.get("sitemap_url"),
            html_scrape_config=s.get("html_scrape_config"),
            is_active=s.get("is_active", True),
            priority=s.get("priority", 100),
            requires_browser=s.get("requires_browser", False),
            crawl_config=s.get("crawl_config"),
            crawl_enabled=s.get("crawl_enabled", False),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(source)
        added += 1
        logger.info(f"[Seed] Added source: {s['name']}")

    await session.flush()
    logger.info(f"[Seed] Sources — added: {added}, updated: {updated}")


async def seed_locations(session):
    """Pre-seed BD geo hierarchy from locations_bd.json."""
    try:
        with open(BD_GEO_PATH, "r", encoding="utf-8") as f:
            bd_geo = json.load(f)
    except FileNotFoundError:
        logger.warning(f"[Seed] {BD_GEO_PATH} not found, skipping BD locations")
        return
    except Exception as e:
        logger.error(f"[Seed] Cannot load BD geo: {e}")
        return

    count = 0
    for division_name, division_data in bd_geo.items():
        div_result = await session.execute(
            select(Location).where(
                Location.name == division_name,
                Location.type == "division",
            ).limit(1)
        )
        division = div_result.scalar_one_or_none()
        if not division:
            division = Location(
                name=division_name,
                type="division",
                parent_id=None,
                country_code="BD",
            )
            session.add(division)
            await session.flush()
            count += 1

        for district_name, district_data in division_data.get("districts", {}).items():
            dist_result = await session.execute(
                select(Location).where(
                    Location.name == district_name,
                    Location.type == "district",
                    Location.parent_id == division.id,
                ).limit(1)
            )
            district = dist_result.scalar_one_or_none()
            if not district:
                district = Location(
                    name=district_name,
                    type="district",
                    parent_id=division.id,
                    country_code="BD",
                )
                session.add(district)
                await session.flush()
                count += 1

            for city_name in district_data.get("cities", {}).keys():
                city_result = await session.execute(
                    select(Location).where(
                        Location.name == city_name,
                        Location.type == "city",
                        Location.parent_id == district.id,
                    ).limit(1)
                )
                city = city_result.scalar_one_or_none()
                if not city:
                    city = Location(
                        name=city_name,
                        type="city",
                        parent_id=district.id,
                        country_code="BD",
                    )
                    session.add(city)
                    await session.flush()
                    count += 1

    logger.info(f"[Seed] BD locations added: {count}")


async def seed_countries(session):
    """Pre-seed international countries from countries.json."""
    try:
        with open(COUNTRIES_PATH, "r", encoding="utf-8") as f:
            countries = json.load(f)
    except FileNotFoundError:
        logger.warning(f"[Seed] {COUNTRIES_PATH} not found, skipping countries")
        return
    except Exception as e:
        logger.error(f"[Seed] Cannot load countries: {e}")
        return

    count = 0
    for country_name, data in countries.items():
        result = await session.execute(
            select(Location).where(
                Location.name == country_name,
                Location.type == "country",
            ).limit(1)
        )
        existing = result.scalar_one_or_none()
        if not existing:
            loc = Location(
                name=country_name,
                type="country",
                parent_id=None,
                country_code=data.get("code", "") if isinstance(data, dict) else "",
            )
            session.add(loc)
            count += 1
    await session.flush()
    logger.info(f"[Seed] Countries added: {count}")


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("[Seed] Tables ensured.")

    async with AsyncSessionLocal() as session:
        async with session.begin():
            await seed_sources(session)
            await seed_locations(session)
            await seed_countries(session)

    logger.info("[Seed] ✓ Database seeded successfully.")


if __name__ == "__main__":
    asyncio.run(main())
