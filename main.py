"""
Main entry point for the News Scraper Engine.

Usage:
  python main.py               → Start the scheduler (runs every N minutes)
  python main.py --once        → Run a single scrape immediately and exit
  python main.py --init-db     → Initialize the PostgreSQL schema and exit
"""

import asyncio
import sys
import signal
from dotenv import load_dotenv

load_dotenv()

from utils.logger import get_logger

logger = get_logger("main")


async def init_db():
    """Create all tables in PostgreSQL."""
    from db.session import engine
    from db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("[DB] All tables created successfully.")


async def run_once():
    """Run a single scrape pass and exit."""
    from runner import run_scraper
    logger.info("[Main] Running single scrape pass...")
    await run_scraper()
    logger.info("[Main] Single pass complete.")


async def run_scheduler():
    """Start the scheduler and keep the event loop alive."""
    from scheduler import create_scheduler
    from runner import run_scraper

    scheduler = create_scheduler()
    scheduler.start()
    logger.info("[Main] Scheduler started. Press Ctrl+C to stop.")

    # Run immediately on startup
    logger.info("[Main] Running initial scrape on startup...")
    await run_scraper()

    # Keep alive
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("[Main] Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=False)
        logger.info("[Main] Scheduler shut down. Goodbye.")


def main():
    args = sys.argv[1:]

    if "--init-db" in args:
        asyncio.run(init_db())
    elif "--once" in args:
        asyncio.run(run_once())
    else:
        asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
