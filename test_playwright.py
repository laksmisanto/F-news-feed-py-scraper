"""
Standalone Playwright smoke test — verify the browser launches and
can render a JS-heavy site without touching the DB or running the
full pipeline.

Usage:
    python test_playwright.py https://www.jamuna.tv
    python test_playwright.py https://www.somoynews.tv networkidle
    python test_playwright.py https://www.somoynews.tv networkidle 5

Args:
    1. url          (required)  — URL to render
    2. wait_until   (optional)  — load / domcontentloaded / networkidle / commit
                                  default: domcontentloaded
    3. max_links    (optional)  — how many article-looking links to list
                                  default: 10

Output:
    - HTML size after rendering
    - Number of <a> tags found
    - First N article-looking URLs
    - Title from <title> tag

Exit codes:
    0 — render succeeded
    1 — render returned None
    2 — wrong arguments
"""

import asyncio
import sys
from types import SimpleNamespace
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup

from utils.browser import PlaywrightManager
from fetchers.crawler import CrawlerFetcher
from utils.logger import get_logger

logger = get_logger("test_playwright")


def parse_args():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    url = sys.argv[1]
    wait_until = sys.argv[2] if len(sys.argv) > 2 else "domcontentloaded"
    max_links = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    return url, wait_until, max_links


def looks_like_article(href: str, base_host: str) -> bool:
    """Reuse the crawler's heuristic to classify URLs."""
    classifier = CrawlerFetcher(timeout=30)
    try:
        parsed = urlparse(href)
        if parsed.netloc.lower() != base_host:
            return False
        return classifier._is_article_url(href, configured=None)
    except Exception:
        return False


async def main():
    url, wait_until, max_links = parse_args()

    print(f"\n🎭  Playwright test")
    print(f"   url        = {url}")
    print(f"   wait_until = {wait_until}")
    print()

    try:
        async with PlaywrightManager() as pw:
            async with pw.context() as ctx:
                print("   Rendering...")
                html = await ctx.render(url, wait_until=wait_until, timeout=30000)
    except Exception as e:
        print(f"\n❌  Playwright failed to launch: {e}")
        print("\n   Common causes:")
        print("   • Chromium not installed → run: playwright install chromium")
        print("   • Missing system deps → see README-PLAYWRIGHT.md")
        sys.exit(1)

    if not html:
        print("\n❌  Render returned no HTML.")
        print("\n   Things to try:")
        print(f"   • Different wait strategy: python test_playwright.py {url} networkidle")
        print(f"   • Site may be blocking automated browsers (Cloudflare/Akamai)")
        sys.exit(1)

    print(f"\n✅  Render succeeded ({len(html):,} bytes)")

    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    print(f"   Page title : {title_tag.get_text(strip=True) if title_tag else '(none)'}")

    base_host = urlparse(url).netloc.lower()
    all_links = soup.find_all("a", href=True)
    print(f"   <a> tags   : {len(all_links)}")

    article_urls = []
    seen = set()
    for a in all_links:
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(url, href).split("#", 1)[0]
        if full in seen:
            continue
        seen.add(full)
        if looks_like_article(full, base_host):
            article_urls.append(full)
            if len(article_urls) >= max_links:
                break

    print(f"   Article-like URLs: {len(article_urls)}")
    print()
    if article_urls:
        print("─" * 70)
        for i, u in enumerate(article_urls, 1):
            print(f"  {i:2d}. {u}")
        print("─" * 70)
        print()
        print("✅  Ready to enable this source with requires_browser=True")
    else:
        print("⚠   No article-like URLs found on the rendered page.")
        print()
        # Dump all same-domain URLs so we can work out what pattern to add
        same_domain = []
        seen2 = set()
        for a in all_links:
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            full = urljoin(url, href).split("#", 1)[0]
            if full in seen2:
                continue
            seen2.add(full)
            try:
                if urlparse(full).netloc.lower() == base_host and urlparse(full).path not in ("/", ""):
                    same_domain.append(full)
            except Exception:
                continue

        print(f"   All same-domain URLs found ({len(same_domain)} unique):")
        print("─" * 70)
        for u in same_domain[:40]:
            print(f"   {u}")
        if len(same_domain) > 40:
            print(f"   ... and {len(same_domain) - 40} more")
        print("─" * 70)
        print()
        print("   Copy a few article URLs above and tell me the pattern,")
        print("   then I can add the right article_url_patterns to crawl_config.")

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
