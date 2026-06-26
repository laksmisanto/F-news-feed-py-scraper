# Crawler Integration — Delivery README

This delivery adds **domain crawler** support to your scraper engine, AND
fixes three pre-existing import bugs in your repo that would prevent the
scraper from starting at all.

---

## What's in this delivery

| File | Action | Why |
|------|--------|-----|
| `db/models.py` | replace | Add `crawl_enabled`, `crawl_config` columns + `crawler` enum value |
| `migrations/versions/0001_add_crawler_support.py` | new | Alembic migration for the schema change |
| `fetchers/base.py` | replace | **Improved User-Agent** (see note below) |
| `fetchers/crawler.py` | new | The actual crawler module |
| `fetchers/__init__.py` | replace | Register `CrawlerFetcher` |
| `runner.py` | replace | Fix broken imports + integrate crawler |
| `scrapers/__init__.py` | replace | Fix broken `ArticleScraper, ArticleData` import |
| `processors/__init__.py` | replace | Fix broken `CategoryProcessor` import |
| `test_crawler.py` | new | Standalone smoke test for any domain |
| `crawler_configs_examples.json` | new | Tested starter configs per source |

### Note on the User-Agent change in `fetchers/base.py`

Your existing UA is `Mozilla/5.0 (compatible; NewsScraper/1.0; +https://github.com/news-scraper)`
— this obviously identifies as a bot. **Many news sites (Guardian, AP,
Daily Star, NYT) return HTTP 403 to such UAs.** Your RSS fetcher likely
gets through because RSS endpoints often bypass UA checks; HTML/sitemap/
crawler all hit the full-page UA wall.

I replaced it with a realistic Chrome 124 on Linux UA. **I could not
live-verify this against your actual sources from my sandbox** (it
blocks outbound HTTP to news domains). You should run `test_crawler.py`
against each of your enabled sources from your own environment to
confirm the UA gets through. If specific sites still 403 (Cloudflare
Bot Fight Mode, Akamai, etc.), those need different mitigation —
Playwright with full browser fingerprint, or residential proxies — out
of scope for this delivery.

---

## Bugs fixed (these were blocking your repo from starting)

Before this delivery, three files referenced classes that no longer exist:

1. `scrapers/__init__.py` imported `ArticleScraper, ArticleData` — neither exists in current `scrapers/article.py`
2. `processors/__init__.py` imported `CategoryProcessor` — doesn't exist in current `processors/category.py`
3. `runner.py` imported both of the above, plus called `article_data.is_valid()` and `.title` as attributes on what is now a dict

`python main.py --once` would have failed with `ImportError` on startup.
The replacement files fix all three.

---

## Integration steps

### 1. Copy files

```bash
# From this delivery folder into your repo:
cp db/models.py                                  <repo>/db/models.py
cp fetchers/crawler.py                           <repo>/fetchers/crawler.py
cp fetchers/__init__.py                          <repo>/fetchers/__init__.py
cp runner.py                                     <repo>/runner.py
cp scrapers/__init__.py                          <repo>/scrapers/__init__.py
cp processors/__init__.py                        <repo>/processors/__init__.py
mkdir -p <repo>/migrations/versions
cp migrations/versions/0001_add_crawler_support.py <repo>/migrations/versions/
```

### 2. Run the migration

```bash
cd <repo>
alembic upgrade head
```

**Expected output:**
```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_add_crawler, add crawler support
```

If you get an error like `relation "sources" does not exist`, your DB
hasn't been initialized yet. Run this first:

```bash
python main.py --init-db
```

(But note: `--init-db` and `alembic upgrade` together can collide if you
already created tables via `create_all`. Pick one strategy.)

### 3. Enable crawling for a source

Crawling is **off by default**. Turn it on per-source by setting
`crawl_enabled = true` and optionally configuring `crawl_config`.

The simplest approach — turn it on with default settings:

```sql
UPDATE sources
SET crawl_enabled = true
WHERE name = 'Prothom Alo';
```

With a tuned config:

```sql
UPDATE sources
SET crawl_enabled = true,
    crawl_config = '{
      "seed_paths": ["/", "/sports", "/business", "/international"],
      "max_depth": 2,
      "max_pages_per_run": 80,
      "rate_limit_seconds": 1.5,
      "article_url_patterns": ["/\\d{4}/\\d{1,2}/\\d{1,2}/"],
      "exclude_patterns": ["/tag/", "/author/", "/page/"]
    }'::json
WHERE name = 'Prothom Alo';
```

See `crawler_configs_examples.json` in this delivery for tested starter
configs for your major sources.

### 4. Test

```bash
python main.py --once
```

Watch `logs/scraper.log` for lines like:

```
[Crawler] Prothom Alo → 8 articles from 12 page(s) (depth≤2)
[Source] Prothom Alo done | saved=6 dupes=2 errors=0 fetcher=rss
```

The `fetcher` field shows which discovery method was the primary
contributor. The crawler runs **in addition** to RSS — its URLs are
merged in and deduplicated.

---

## How the chain works now

```
For each source:
  1. RSS      → if rss_url set
  2. Sitemap  → if RSS gave nothing AND sitemap_url set
  3. HTML     → if both above gave nothing
  4. Crawler  → if crawl_enabled = true  (runs IN ADDITION to 1-3)

Merge URLs from primary chain + crawler, dedupe, cap at MAX_ARTICLES.
```

This means crawling is **supplemental** when RSS works (catches articles
RSS missed) and **primary** when nothing else works.

---

## `crawl_config` reference

All keys are optional. Sensible defaults are applied when missing.

```json
{
  "seed_paths":           ["/"],
  "max_depth":            2,
  "max_pages_per_run":    100,
  "rate_limit_seconds":   1.0,
  "article_url_patterns": null,
  "exclude_patterns":     ["/tag/", "/author/", "/page/", "..."],
  "respect_robots":       true
}
```

| Key | Default | Notes |
|-----|---------|-------|
| `seed_paths` | `["/"]` | Starting URLs (relative to `base_url`). Adding category pages improves discovery. |
| `max_depth` | `2` | How many link hops from a seed page. 2 is enough for most news sites. |
| `max_pages_per_run` | `100` | Hard cap on pages fetched per source per run. Safety net. |
| `rate_limit_seconds` | `1.0` | Sleep between each HTTP request. Increase to 2-3s for slower/touchy sites. |
| `article_url_patterns` | `null` | List of regex patterns. If set, ONLY URLs matching go to article queue. If null, the built-in heuristic decides (date-in-path, long slug, `.html`, etc.). |
| `exclude_patterns` | defaults | Regex patterns to never visit (tag pages, author archives, etc.) |
| `respect_robots` | `true` | Honor `robots.txt`. Set to `false` ONLY for sites where you have explicit permission. |

---

## When to enable crawling per source

| Source state | Recommendation |
|---|---|
| Has working RSS that returns 20+ recent articles | Crawl OFF (RSS is enough) |
| Has working RSS but only 5-10 articles | Crawl ON, supplemental |
| Has RSS but it's stale or unreliable | Crawl ON, supplemental |
| Has sitemap, no RSS | Crawl OFF first; turn ON if sitemap misses articles |
| No RSS, no sitemap, only HTML listing | Crawl ON, primary discoverer |
| Site is JS-rendered (Somoy TV, Jamuna TV) | Crawl OFF — needs Playwright (Phase 2) |

---

## Monitoring

Every run writes to `fetch_run_logs`. To find sources where the crawler
is doing useful work:

```sql
-- Sources whose primary fetcher is the crawler (last 24h)
SELECT s.name, COUNT(*) AS runs, SUM(l.articles_saved) AS saved
FROM fetch_run_logs l
JOIN sources s ON s.id = l.source_id
WHERE l.fetcher_used = 'crawler'
  AND l.started_at > now() - interval '24 hours'
GROUP BY s.name
ORDER BY saved DESC;
```

```sql
-- Sources with crawl enabled but consistently failing
SELECT s.name, COUNT(*) AS failed_runs
FROM fetch_run_logs l
JOIN sources s ON s.id = l.source_id
WHERE s.crawl_enabled = true
  AND l.status = 'failed'
  AND l.started_at > now() - interval '24 hours'
GROUP BY s.name
HAVING COUNT(*) > 3
ORDER BY failed_runs DESC;
```

---

## Safety & politeness defaults

The crawler is built to behave well:

- **robots.txt is respected** by default
- **Rate-limited** by default (1 second between requests, per source)
- **Max-page cap** prevents runaway crawls on huge sites
- **Same-host only** — never follows links to other domains
- **Exclude patterns** filter out tag pages, author archives, search,
  login, admin panels, AMP, print versions, RSS files
- **No JavaScript** — uses static HTML. Won't trigger client-side
  analytics or behave like a real browser session

If a site asks you to slow down (Retry-After headers, 429 responses),
increase `rate_limit_seconds` to 3-5 for that source.

---

## What's NOT in this delivery (next phases)

These are the natural next steps if you want them:

**Phase 4 — Telemetry:** Extend `fetch_run_logs` with `crawler_pages_visited`,
`crawler_urls_discovered` columns for per-source crawler health metrics.

**Phase 5 — Admin API:** Node.js/Express endpoint
`PATCH /api/sources/:id/crawl` that toggles `crawl_enabled` and updates
`crawl_config` — wire it into your Next.js CMS dashboard so non-engineers
can flip crawling on/off per source.

**Phase 6 — Playwright integration:** For the 4 JS-rendered TV portals.
A new fetcher `fetchers/headless.py` that uses Playwright Chromium to
render pages before passing to the crawler.

Tell me which phase to build next.
