"""
Standalone crawler test — verify CrawlerFetcher works against a real
domain WITHOUT touching the database or running the full pipeline.

Usage:
    python test_crawler.py https://www.prothomalo.com
    python test_crawler.py https://www.thedailystar.net 5
    python test_crawler.py https://bdnews24.com 10 sports,politics,business

Args:
    1. base_url      (required)  — domain root
    2. max_articles  (optional)  — default 10
    3. seed_paths    (optional)  — comma-separated, default "/"

Output:
    - Lists each discovered article URL
    - Prints stats: pages fetched, articles found, time taken
    - Exit code 0 if articles found, 1 if zero
"""

import asyncio
import sys
import time
from types import SimpleNamespace

import httpx

# Import the crawler — this file lives at repo root same as main.py
from fetchers.crawler import CrawlerFetcher
from utils.logger import get_logger

logger = get_logger("test_crawler")


def parse_args():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)

    base_url = sys.argv[1].rstrip("/")
    max_articles = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    if len(sys.argv) > 3:
        seed_paths = ["/" + s.lstrip("/") for s in sys.argv[3].split(",") if s.strip()]
    else:
        seed_paths = ["/"]

    return base_url, max_articles, seed_paths


def make_fake_source(base_url: str, seed_paths: list[str]):
    """
    Build a minimal duck-typed source the crawler expects.
    We don't touch the DB — this is a plain Python object that
    looks enough like a Source ORM row for the crawler.
    """
    return SimpleNamespace(
        name=base_url.split("//", 1)[-1].split("/", 1)[0],
        base_url=base_url,
        crawl_enabled=True,
        crawl_config={
            "seed_paths": seed_paths,
            "max_depth": 2,
            "max_pages_per_run": 50,
            "rate_limit_seconds": 1.0,
            "respect_robots": True,
        },
    )


async def main():
    base_url, max_articles, seed_paths = parse_args()
    source = make_fake_source(base_url, seed_paths)

    print(f"\n🕷  Crawling {base_url}")
    print(f"   seed_paths   = {seed_paths}")
    print(f"   max_articles = {max_articles}")
    print(f"   max_depth    = {source.crawl_config['max_depth']}")
    print(f"   rate_limit   = {source.crawl_config['rate_limit_seconds']}s")
    print()

    crawler = CrawlerFetcher(timeout=30)
    start = time.time()

    async with httpx.AsyncClient() as client:
        urls = await crawler.fetch_urls(source, client, max_articles=max_articles)

    elapsed = time.time() - start

    print()
    print("─" * 70)
    print(f"  Found {len(urls)} article URL(s) in {elapsed:.1f}s")
    print("─" * 70)
    for i, u in enumerate(urls, 1):
        print(f"  {i:2d}. {u}")
    print()

    if not urls:
        print("⚠  No articles discovered. Things to try:")
        print("   • Add category seed paths:   python test_crawler.py <url> 10 sports,business")
        print("   • Increase max_depth in the script (currently 2)")
        print("   • Check the site isn't fully JS-rendered (view-source shows little)")
        print("   • Verify robots.txt allows crawling: <site>/robots.txt")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
