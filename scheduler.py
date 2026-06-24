"""
Scheduler — APScheduler AsyncIOScheduler.
Triggers run_scraper() every N minutes (default 10, configurable via .env).
"""

import asyncio
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from runner import run_scraper
from utils.logger import get_logger

load_dotenv()

logger = get_logger("scheduler")

INTERVAL_MINUTES = int(os.getenv("SCRAPER_INTERVAL_MINUTES", "10"))


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        func=run_scraper,
        trigger=IntervalTrigger(minutes=INTERVAL_MINUTES),
        id="news_scraper_job",
        name="News Scraper",
        replace_existing=True,
        max_instances=1,          # Never run two instances at once
        coalesce=True,            # If a run was missed, only trigger once
        misfire_grace_time=60,    # Allow up to 60s late start
    )

    logger.info(f"[Scheduler] Job registered: every {INTERVAL_MINUTES} minutes")
    return scheduler
