"""
Seed script — populates the database with:
  1. Sample Bangla and English news sources
  2. Pre-seeds BD location hierarchy from locations_bd.json
  3. Pre-seeds countries from countries.json

Run: python seed.py
"""

import asyncio
import json
import os
from datetime import datetime

from db.session import AsyncSessionLocal, engine
from db.models import Base, Source, Location, LanguageEnum
from sqlalchemy import select
from utils.logger import get_logger

logger = get_logger("seed")

BD_GEO_PATH = os.path.join("config", "keywords", "locations_bd.json")
COUNTRIES_PATH = os.path.join("config", "keywords", "countries.json")

# ---------------------------------------------------------------------------
# SOURCES
# Customize this list with your actual 20-25 sources.
# html_scrape_config: fill in actual CSS selectors per source.
# ---------------------------------------------------------------------------

SOURCES = [
    # ── BANGLA SOURCES ──────────────────────────────────────────────────────
    {
        "name": "Prothom Alo",
        "language": "bn",
        "base_url": "https://www.prothomalo.com",
        "rss_url": "https://www.prothomalo.com/feed",
        "sitemap_url": "https://www.prothomalo.com/sitemap.xml",
        "html_scrape_config": {
            "article_list": "a.title-link",
            "title": "h1",
            "body": "div.story-element-text",
            "image": "figure img",
            "date": "time"
        },
        "is_active": True,
        "priority": 1,
    },
    {
        "name": "Daily Star BD",
        "language": "en",
        "base_url": "https://www.thedailystar.net",
        "rss_url": "https://www.thedailystar.net/arcio/rss/",
        "sitemap_url": "https://www.thedailystar.net/sitemap.xml",
        "html_scrape_config": {
            "article_list": "h3.title a",
            "title": "h1",
            "body": "div.field-items",
            "image": ".field-type-image img",
            "date": "span.date-display-single"
        },
        "is_active": True,
        "priority": 2,
    },
    {
        "name": "Kaler Kantho",
        "language": "bn",
        "base_url": "https://www.kalerkantho.com",
        "rss_url": "https://www.kalerkantho.com/rss.xml",
        "sitemap_url": "https://www.kalerkantho.com/sitemap.xml",
        "html_scrape_config": {
            "article_list": "h3 a, h2 a",
            "title": "h1",
            "body": "div.news-content",
            "image": "div.news-img img",
            "date": "span.time"
        },
        "is_active": True,
        "priority": 3,
    },
    {
        "name": "Jugantor",
        "language": "bn",
        "base_url": "https://www.jugantor.com",
        "rss_url": "https://www.jugantor.com/feed",
        "sitemap_url": None,
        "html_scrape_config": {
            "article_list": "h3 a",
            "title": "h1",
            "body": "div.news-details-body",
            "image": "div.news-details-img img",
            "date": "span.time-area"
        },
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
        "name": "Samakal",
        "language": "bn",
        "base_url": "https://samakal.com",
        "rss_url": "https://samakal.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 6,
    },
    {
        "name": "Manab Zamin",
        "language": "bn",
        "base_url": "https://mzamin.com",
        "rss_url": "https://mzamin.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 7,
    },
    {
        "name": "Inqilab",
        "language": "bn",
        "base_url": "https://www.dailyinqilab.com",
        "rss_url": "https://www.dailyinqilab.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 8,
    },
    {
        "name": "Bangladesh Pratidin",
        "language": "bn",
        "base_url": "https://www.bd-pratidin.com",
        "rss_url": "https://www.bd-pratidin.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 9,
    },
    {
        "name": "Naya Diganta",
        "language": "bn",
        "base_url": "https://www.dailynayadiganta.com",
        "rss_url": "https://www.dailynayadiganta.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 10,
    },
    {
        "name": "Bhorer Kagoj",
        "language": "bn",
        "base_url": "https://www.bhorerkagoj.com",
        "rss_url": "https://www.bhorerkagoj.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 11,
    },
    {
        "name": "Dainik Amader Shomoy",
        "language": "bn",
        "base_url": "https://www.dainikamadershomoy.com",
        "rss_url": "https://www.dainikamadershomoy.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 12,
    },
    {
        "name": "Bangla Tribune",
        "language": "bn",
        "base_url": "https://www.banglatribune.com",
        "rss_url": "https://www.banglatribune.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 13,
    },
    {
        "name": "Risingbd",
        "language": "bn",
        "base_url": "https://www.risingbd.com",
        "rss_url": "https://www.risingbd.com/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 14,
    },
    {
        "name": "Desh Rupantor",
        "language": "bn",
        "base_url": "https://www.deshrupantor.com",
        "rss_url": "https://www.deshrupantor.com/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 15,
    },
    # ── ENGLISH SOURCES ─────────────────────────────────────────────────────
    {
        "name": "Financial Express BD",
        "language": "en",
        "base_url": "https://thefinancialexpress.com.bd",
        "rss_url": "https://thefinancialexpress.com.bd/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 16,
    },
    {
        "name": "Business Standard BD",
        "language": "en",
        "base_url": "https://www.tbsnews.net",
        "rss_url": "https://www.tbsnews.net/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 17,
    },
    {
        "name": "New Age BD",
        "language": "en",
        "base_url": "https://www.newagebd.net",
        "rss_url": "https://www.newagebd.net/rss.xml",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 18,
    },
    {
        "name": "Dhaka Tribune",
        "language": "en",
        "base_url": "https://www.dhakatribune.com",
        "rss_url": "https://www.dhakatribune.com/rss.xml",
        "sitemap_url": "https://www.dhakatribune.com/sitemap.xml",
        "html_scrape_config": None,
        "is_active": True,
        "priority": 19,
    },
    {
        "name": "UNB News",
        "language": "en",
        "base_url": "https://unb.com.bd",
        "rss_url": "https://unb.com.bd/feed",
        "sitemap_url": None,
        "html_scrape_config": None,
        "is_active": True,
        "priority": 20,
    },
]


async def seed_sources(session):
    count = 0
    for s in SOURCES:
        result = await session.execute(
            select(Source).where(Source.base_url == s["base_url"])
        )
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(f"[Seed] Source already exists: {s['name']}")
            continue

        source = Source(
            name=s["name"],
            language=LanguageEnum(s["language"]),
            base_url=s["base_url"],
            rss_url=s.get("rss_url"),
            sitemap_url=s.get("sitemap_url"),
            html_scrape_config=s.get("html_scrape_config"),
            is_active=s.get("is_active", True),
            priority=s.get("priority", 10),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(source)
        count += 1

    await session.flush()
    logger.info(f"[Seed] Sources added: {count}")


async def seed_locations(session):
    """Pre-seed BD geo hierarchy from locations_bd.json."""
    try:
        with open(BD_GEO_PATH, "r", encoding="utf-8") as f:
            bd_geo = json.load(f)
    except Exception as e:
        logger.error(f"[Seed] Cannot load BD geo: {e}")
        return

    count = 0
    for division_name, division_data in bd_geo.items():
        # Division
        div_result = await session.execute(
            select(Location).where(
                Location.name == division_name,
                Location.type == "division"
            )
        )
        division = div_result.scalar_one_or_none()
        if not division:
            division = Location(
                name=division_name, type="division",
                parent_id=None, country_code="BD"
            )
            session.add(division)
            await session.flush()
            count += 1

        for district_name, district_data in division_data.get("districts", {}).items():
            # District
            dist_result = await session.execute(
                select(Location).where(
                    Location.name == district_name,
                    Location.type == "district",
                    Location.parent_id == division.id
                )
            )
            district = dist_result.scalar_one_or_none()
            if not district:
                district = Location(
                    name=district_name, type="district",
                    parent_id=division.id, country_code="BD"
                )
                session.add(district)
                await session.flush()
                count += 1

            for city_name in district_data.get("cities", {}).keys():
                city_result = await session.execute(
                    select(Location).where(
                        Location.name == city_name,
                        Location.type == "city",
                        Location.parent_id == district.id
                    )
                )
                city = city_result.scalar_one_or_none()
                if not city:
                    city = Location(
                        name=city_name, type="city",
                        parent_id=district.id, country_code="BD"
                    )
                    session.add(city)
                    await session.flush()
                    count += 1

    logger.info(f"[Seed] BD locations added: {count}")


async def seed_countries(session):
    """Pre-seed international countries."""
    try:
        with open(COUNTRIES_PATH, "r", encoding="utf-8") as f:
            countries = json.load(f)
    except Exception as e:
        logger.error(f"[Seed] Cannot load countries: {e}")
        return

    count = 0
    for country_name, data in countries.items():
        result = await session.execute(
            select(Location).where(
                Location.name == country_name,
                Location.type == "country"
            )
        )
        existing = result.scalar_one_or_none()
        if not existing:
            loc = Location(
                name=country_name,
                type="country",
                parent_id=None,
                country_code=data.get("code", "")
            )
            session.add(loc)
            count += 1

    await session.flush()
    logger.info(f"[Seed] Countries added: {count}")


async def main():
    # Ensure tables exist
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
