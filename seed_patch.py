"""
SEED.PY PATCH — Update the 4 TV portal entries in your seed.py.

This is NOT a full file replacement. Open your existing seed.py and replace
ONLY the 4 TV portal blocks at the end (Somoy News TV, Jamuna TV, Ekattor TV,
DBC News) with the versions below.

The changes per entry:
  - is_active        : False → True   (activate the source)
  - requires_browser : True            (new field — forces Playwright)
  - crawl_config     : filled in       (seed paths, Playwright wait strategy)

After replacing, also update your seed_sources() function so it copies the
requires_browser field. See the bottom of this file for that helper change.
"""

# ═══════════════════════════════════════════════════════════════════════════
# REPLACE these 4 entries at the end of your SOURCES list in seed.py
# ═══════════════════════════════════════════════════════════════════════════

TV_PORTAL_SOURCES = [
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
            "seed_paths": ["/", "/latest", "/national", "/politics", "/sports"],
            "playwright_wait": "networkidle",
            "max_pages_per_run": 8,
            "rate_limit_seconds": 1.5,
            "exclude_patterns": ["/tag/", "/video/", "/photo/", "/category/"]
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
            "seed_paths": ["/", "/news", "/national", "/politics", "/sports"],
            "playwright_wait": "networkidle",
            "max_pages_per_run": 8,
            "rate_limit_seconds": 1.5,
            "exclude_patterns": ["/tag/", "/video/", "/program/", "/category/"]
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
            "seed_paths": ["/", "/news", "/national", "/politics", "/sports"],
            "playwright_wait": "networkidle",
            "max_pages_per_run": 8,
            "rate_limit_seconds": 1.5,
            "exclude_patterns": ["/tag/", "/video/", "/program/", "/category/"]
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
            "seed_paths": ["/", "/news", "/national", "/politics", "/sports"],
            "playwright_wait": "networkidle",
            "max_pages_per_run": 8,
            "rate_limit_seconds": 1.5,
            "exclude_patterns": ["/tag/", "/video/", "/program/", "/category/"]
        },
        "priority": 27,
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# ALSO update your seed_sources() function in seed.py — find this section
# and update it to handle the new requires_browser + crawl_config fields:
# ═══════════════════════════════════════════════════════════════════════════

SEED_SOURCES_PATCH = """
# In seed.py → seed_sources() function:
# Inside the loop where you build the Source(...) constructor,
# add these two lines:

source = Source(
    name=s["name"],
    language=LanguageEnum(s["language"]),
    base_url=s["base_url"],
    rss_url=s.get("rss_url"),
    sitemap_url=s.get("sitemap_url"),
    html_scrape_config=s.get("html_scrape_config"),
    is_active=s.get("is_active", True),
    priority=s.get("priority", 100),
    # ─── ADD THESE TWO LINES ───
    requires_browser=s.get("requires_browser", False),
    crawl_config=s.get("crawl_config"),
    crawl_enabled=s.get("crawl_enabled", False),
    # ───────────────────────────
    created_at=datetime.now(timezone.utc),
    updated_at=datetime.now(timezone.utc),
)

# Also update the existing-row update branch to copy the new fields:

if existing:
    changed = False
    if existing.rss_url != s.get("rss_url"):
        existing.rss_url = s.get("rss_url"); changed = True
    if existing.sitemap_url != s.get("sitemap_url"):
        existing.sitemap_url = s.get("sitemap_url"); changed = True
    if existing.html_scrape_config != s.get("html_scrape_config"):
        existing.html_scrape_config = s.get("html_scrape_config"); changed = True
    # ─── ADD THESE THREE BLOCKS ───
    if existing.requires_browser != s.get("requires_browser", False):
        existing.requires_browser = s.get("requires_browser", False); changed = True
    if existing.crawl_config != s.get("crawl_config"):
        existing.crawl_config = s.get("crawl_config"); changed = True
    if existing.is_active != s.get("is_active", True):
        existing.is_active = s.get("is_active", True); changed = True
    # ──────────────────────────────
    if changed:
        existing.updated_at = datetime.now(timezone.utc)
"""
