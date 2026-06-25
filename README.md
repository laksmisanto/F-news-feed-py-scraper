# News Scraper Engine

A production-grade, async Python news scraper engine that fetches, processes, and stores news articles from 20+ Bangla and English sources into PostgreSQL. Runs automatically every 10–15 minutes.

No frontend. No API. Pure scraping engine — designed to feed a separate backend API that reads from the same database.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Database Schema](#database-schema)
- [How It Works](#how-it-works)
- [Setup & Installation](#setup--installation)
- [Configuration](#configuration)
- [Running the Engine](#running-the-engine)
- [Adding / Managing Sources](#adding--managing-sources)
- [Category System](#category-system)
- [Location Detection](#location-detection)
- [Tag Extraction](#tag-extraction)
- [Logs & Monitoring](#logs--monitoring)
- [Extending the Engine](#extending-the-engine)

---

## Features

- **Async-first** — uses `asyncio` + `httpx` for concurrent source processing
- **3-tier fetcher chain** — RSS → Sitemap → HTML fallback per source
- **URL-based deduplication** — no article is stored twice
- **Auto category detection** — keyword matching with parent/child hierarchy (e.g. Sports → Cricket)
- **Dynamic tag extraction** — open tag system, no fixed vocabulary
- **BD geo location detection** — City → District → Division hierarchy for Bangla sources
- **International location detection** — country-level for English sources
- **Dashboard-controlled sources** — enable/disable/add/remove via DB, no redeployment needed
- **Detailed run logging** — per-source, per-run stats stored in `fetch_run_logs`
- **Configurable interval** — 10–15 min via `.env`

---

## Architecture

```
Scheduler (APScheduler)
    └── Every 10–15 min → runner.py
            └── Load active sources (DB)
                └── For each source (concurrent, asyncio):
                        └── Fetcher chain (RSS → Sitemap → HTML)
                                └── Per URL: Dedup check
                                        └── Article scraper (httpx + BS4)
                                                └── Processors (Category + Tag + Location)
                                                        └── Save to PostgreSQL
                                                                └── Write run log
```

### Tech Stack

| Layer            | Library                        |
| ---------------- | ------------------------------ |
| Language         | Python 3.11+                   |
| Scheduler        | APScheduler (AsyncIOScheduler) |
| HTTP client      | httpx (async)                  |
| RSS parsing      | feedparser                     |
| HTML/XML parsing | BeautifulSoup4 + lxml          |
| Database ORM     | SQLAlchemy 2.0 (async)         |
| DB driver        | asyncpg                        |
| Migrations       | Alembic                        |
| Date parsing     | python-dateutil                |

---

## Project Structure

```
news-scraper/
├── main.py                        # Entry point — start scheduler, run once, init DB
├── scheduler.py                   # APScheduler setup and job registration
├── runner.py                      # Per-run orchestrator — processes all sources
├── seed.py                        # DB seeding script — sources + locations + countries
├── requirements.txt
├── .env.example                   # Environment variable template
├── alembic.ini                    # Alembic migration config
│
├── fetchers/
│   ├── base.py                    # Abstract base fetcher (HTTP GET, headers)
│   ├── rss.py                     # RSS fetcher (feedparser)
│   ├── sitemap.py                 # Sitemap fetcher (lxml, handles index + urlset)
│   └── html.py                    # HTML listing page scraper (BS4)
│
├── scrapers/
│   ├── article.py                 # Article content extractor (title, body, image, date)
│   └── cleaner.py                 # Noise removal, plain text conversion
│
├── processors/
│   ├── category.py                # Keyword-based category matcher (parent + child)
│   ├── tag.py                     # Dynamic tag extractor (regex, proper nouns)
│   └── location.py                # BD geo hierarchy + international country detector
│
├── db/
│   ├── models.py                  # SQLAlchemy ORM models (all tables)
│   ├── session.py                 # Async session factory
│   └── queries.py                 # Reusable DB operations
│
├── config/
│   └── keywords/
│       ├── categories.json        # Category → subcategory → keyword map (Bangla + English)
│       ├── locations_bd.json      # BD Division → District → City keyword map
│       └── countries.json         # International country keyword list
│
├── utils/
│   ├── logger.py                  # Centralized logger (console + rotating file)
│   └── helpers.py                 # slugify, date parsing, text cleaning
│
└── migrations/
    ├── env.py                     # Alembic async migration environment
    └── script.py.mako             # Migration file template
```

---

## Database Schema

### `sources`

Stores all news source configurations. Controlled via dashboard.

| Column             | Type        | Description                      |
| ------------------ | ----------- | -------------------------------- |
| id                 | int         | Primary key                      |
| name               | varchar     | Source display name              |
| language           | enum(bn/en) | Content language                 |
| base_url           | varchar     | Homepage URL                     |
| rss_url            | varchar     | RSS feed URL (nullable)          |
| sitemap_url        | varchar     | Sitemap URL (nullable)           |
| html_scrape_config | jsonb       | Per-source CSS selectors         |
| is_active          | bool        | Enable/disable without deleting  |
| priority           | int         | Processing order (lower = first) |

`html_scrape_config` example:

```json
{
  "listing_url": "https://example.com/news",
  "article_list": ".headline a",
  "title": "h1.article-title",
  "body": "div.article-body",
  "image": "figure img",
  "date": "time.publish-time"
}
```

---

### `articles`

Core table. One row per article URL.

| Column            | Type             | Description                              |
| ----------------- | ---------------- | ---------------------------------------- |
| id                | uuid             | Primary key                              |
| source_id         | int FK           | Source reference                         |
| url               | varchar (unique) | Article URL — used for dedup             |
| title             | text             | Required — article is skipped if missing |
| short_description | text             | Meta description or first paragraph      |
| body              | text             | Full plain text body (nullable)          |
| image_url         | varchar          | Primary image URL (nullable)             |
| language          | enum(bn/en)      | Inherited from source                    |
| published_at      | timestamptz      | Article publish date (UTC)               |
| scraped_at        | timestamptz      | When the scraper saved it                |
| is_published      | bool             | Soft toggle for backend                  |

---

### `categories`

Self-referencing table for parent → child hierarchy.

| Column    | Type             | Description                   |
| --------- | ---------------- | ----------------------------- |
| id        | int              | Primary key                   |
| name      | varchar (unique) | e.g. "Sports", "Cricket"      |
| slug      | varchar (unique) | URL-safe identifier           |
| parent_id | int FK (self)    | Null for top-level categories |

Example rows:

```
id=1  name="খেলাধুলা"   parent_id=null    (Sports)
id=2  name="ক্রিকেট"    parent_id=1       (Cricket → under Sports)
id=3  name="রাজনীতি"    parent_id=null    (Politics)
id=4  name="বিএনপি"     parent_id=3       (BNP → under Politics)
```

---

### `tags`

Open/dynamic. New tags are created automatically when detected.

| Column | Type             | Description |
| ------ | ---------------- | ----------- |
| id     | int              | Primary key |
| name   | varchar (unique) | Tag text    |
| slug   | varchar (unique) | URL-safe    |

---

### `locations`

Hierarchical geo table for both BD and international.

| Column       | Type          | Description                          |
| ------------ | ------------- | ------------------------------------ |
| id           | int           | Primary key                          |
| name         | varchar       | Location name                        |
| type         | enum          | city / district / division / country |
| parent_id    | int FK (self) | Parent in hierarchy                  |
| country_code | varchar       | ISO 2-letter code (BD, US, etc.)     |

BD hierarchy example:

```
division: ঢাকা বিভাগ
  └── district: ঢাকা
        └── city: মিরপুর
        └── city: গুলশান
  └── district: গাজীপুর
```

---

### `article_categories`, `article_tags`, `article_locations`

M2M junction tables. Composite primary keys.

---

### `fetch_run_logs`

Per-source, per-run audit trail.

| Column             | Type     | Description                   |
| ------------------ | -------- | ----------------------------- |
| id                 | int      | Primary key                   |
| run_id             | uuid     | Groups all sources in one run |
| source_id          | int FK   | Source reference              |
| started_at         | datetime | When this source started      |
| finished_at        | datetime | When it completed             |
| status             | enum     | success / partial / failed    |
| fetcher_used       | enum     | rss / sitemap / html          |
| urls_found         | int      | Total URLs collected          |
| articles_saved     | int      | Successfully saved            |
| duplicates_skipped | int      | Already existed in DB         |
| errors_skipped     | int      | Scrape or parse failures      |
| error_detail       | text     | Error message if failed       |

---

## How It Works

### Fetcher Chain

Each source is tried in this order. Once one succeeds and returns URLs, the chain stops.

```
1. RSS (feedparser)
   → Parse feed entries, extract up to 10 article links

2. Sitemap (lxml)
   → Parse sitemap XML, handle both index files and direct urlsets
   → Filters by lastmod date (48 hour window) where available

3. HTML Scraper (BS4)
   → Uses CSS selectors from source config
   → Falls back to heuristic link detection if no config
```

### Dedup Check

Before scraping any article, the URL is checked against `articles.url` (indexed, unique constraint). If it exists → skip.

### Article Scraping

For each new URL, the scraper attempts to extract:

- **Title** — config selector → `<h1>` → `og:title` → `<title>` tag
- **Description** — config selector → meta description → first paragraph
- **Body** — config selector → common article selectors → plain text cleaned
- **Image** — config selector → `og:image` → first `<img>` in article
- **Date** — config selector → `<time>` → JSON-LD → meta tags → normalized to UTC

If no title is found, the article is skipped entirely.

### Processing Pipeline

After scraping, the combined text (title + description + body) is passed through three processors:

**Category Processor**

- Loads `categories.json` keyword map
- Matches child keywords first (e.g. "ক্রিকেট" → Cricket)
- If child found → stores child + its parent (Sports) automatically
- Multiple categories per article are supported
- Categories are created on the fly if they don't exist

**Tag Processor**

- Extracts proper nouns and acronyms using regex
- For English: Title Case phrases, ALL CAPS acronyms
- For Bangla: patterns after title markers (মন্ত্রী, সভাপতি, etc.), quoted phrases
- Tags are created dynamically in the DB

**Location Processor**

- Bangla sources: scans for BD geo keywords (City → District → Division)
  - When a city is matched, all three levels are stored (city + district + division)
- English sources: scans for country keywords only
- No match → no location stored (silently skipped)

### Database Write

All inserts for one article happen inside a single transaction:

- `articles` row
- `article_categories` M2M rows
- `article_tags` M2M rows
- `article_locations` M2M rows

---

## Setup & Installation

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 14+ running locally or remote

### 2. Clone and install dependencies

```bash
git clone <your-repo>
cd news-scraper

python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
DATABASE_URL=postgresql+asyncpg://youruser:yourpassword@localhost:5432/news_scraper
SCRAPER_INTERVAL_MINUTES=10
MAX_ARTICLES_PER_SOURCE=10
REQUEST_TIMEOUT=30
MAX_CONCURRENT_SOURCES=10
LOG_LEVEL=INFO
LOG_FILE=logs/scraper.log
```

### 4. Create the database

```sql
CREATE DATABASE news_scraper;
```

### 5. Initialize tables

```bash
python main.py --init-db
```

### 6. Seed initial data (sources + locations)

```bash
python seed.py
```

---

## Running the Engine

### Start the scheduler (production)

```bash
python main.py
```

Runs immediately on startup, then every `SCRAPER_INTERVAL_MINUTES` minutes. Press `Ctrl+C` to stop gracefully.

### Run once (testing / debugging)

```bash
python main.py --once
```

### Run as a background service (Linux)

Create `/etc/systemd/system/news-scraper.service`:

```ini
[Unit]
Description=News Scraper Engine
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/news-scraper
ExecStart=/path/to/news-scraper/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable news-scraper
sudo systemctl start news-scraper
sudo systemctl status news-scraper
```

---

## Configuration

### Environment Variables

| Variable                   | Default            | Description                          |
| -------------------------- | ------------------ | ------------------------------------ |
| `DATABASE_URL`             | required           | PostgreSQL async connection string   |
| `SCRAPER_INTERVAL_MINUTES` | `10`               | How often to run (minutes)           |
| `MAX_ARTICLES_PER_SOURCE`  | `10`               | Max URLs to fetch per source per run |
| `MAX_CONCURRENT_SOURCES`   | `10`               | Max parallel source fetches          |
| `REQUEST_TIMEOUT`          | `30`               | HTTP request timeout (seconds)       |
| `LOG_LEVEL`                | `INFO`             | DEBUG / INFO / WARNING / ERROR       |
| `LOG_FILE`                 | `logs/scraper.log` | Log file path                        |

---

## Adding / Managing Sources

Sources are stored in the `sources` table and can be managed via your dashboard or directly via SQL.

### Add a new source (SQL)

```sql
INSERT INTO sources (name, language, base_url, rss_url, sitemap_url, html_scrape_config, is_active, priority, created_at, updated_at)
VALUES (
  'My News Source',
  'bn',
  'https://www.mynewssource.com',
  'https://www.mynewssource.com/feed',
  NULL,
  '{"article_list": "h3.title a", "title": "h1", "body": "div.content", "image": "figure img", "date": "time"}',
  true,
  25,
  NOW(), NOW()
);
```

### Disable a source

```sql
UPDATE sources SET is_active = false WHERE name = 'My News Source';
```

### No redeployment needed. Changes take effect on the next run.\*\*

---

## Category System

Categories are defined in `config/keywords/categories.json`.

### Structure

```json
{
  "রাজনীতি": {
    "keywords": ["রাজনীতি", "politics", "parliament"],
    "children": {
      "বিএনপি": ["বিএনপি", "BNP", "খালেদা জিয়া"],
      "আওয়ামী লীগ": ["আওয়ামী লীগ", "awami league"]
    }
  }
}
```

### Matching Logic

1. Child keywords are scanned first (most specific)
2. Match found → child category + its parent both assigned to article
3. Only parent keywords match → parent assigned only
4. Multiple parent/child pairs can match one article

### Adding new categories

Just edit `categories.json` — no code changes needed. New categories are created in the DB automatically on first match.

---

## Location Detection

### Bangladesh (Bangla sources)

Config file: `config/keywords/locations_bd.json`

Hierarchy: **Division → District → City**

When "মিরপুর" is detected in an article:

- City: মিরপুর ✓
- District: ঢাকা ✓
- Division: ঢাকা বিভাগ ✓

All three are stored and linked to the article via `article_locations`.

### International (English sources)

Config file: `config/keywords/countries.json`

Only country-level detection. "Gaza" → Palestine (country record).

### No match

If no location keyword is found, no location is stored. The article is saved normally without location data.

---

## Tag Extraction

Tags are extracted dynamically. No fixed list.

### English sources

- Title Case proper nouns: "Sheikh Hasina", "Prime Minister"
- ALL CAPS acronyms: "BNP", "DSE", "GDP"
- Quoted phrases

### Bangla sources

- Phrases after title markers: মন্ত্রী, সভাপতি, রাষ্ট্রপতি, চেয়ারম্যান
- Quoted Bangla phrases
- English acronyms embedded in text

### Limits

- Max 10 tags per article
- Tag length: 3–60 characters
- Common stopwords excluded automatically
- Duplicate tags (same slug) reuse existing DB row

---

## Logs & Monitoring

### Console output

Logs to stdout with timestamp, level, and module name.

### File logs

Rotating log file at `logs/scraper.log` (10MB per file, 5 backups).

### Database logs

Every run writes to `fetch_run_logs`. Query example:

```sql
-- Last 10 run summaries
SELECT
  run_id,
  COUNT(*) AS sources_processed,
  SUM(articles_saved) AS total_saved,
  SUM(duplicates_skipped) AS total_dupes,
  MIN(started_at) AS run_started
FROM fetch_run_logs
GROUP BY run_id
ORDER BY run_started DESC
LIMIT 10;

-- Sources that failed in the last run
SELECT s.name, f.status, f.error_detail, f.fetcher_used
FROM fetch_run_logs f
JOIN sources s ON s.id = f.source_id
WHERE f.run_id = (SELECT run_id FROM fetch_run_logs ORDER BY started_at DESC LIMIT 1)
  AND f.status = 'failed';
```

---

## Extending the Engine

### Support backend API queries

The schema is ready for all common query patterns:

| Query                | How                                               |
| -------------------- | ------------------------------------------------- |
| Latest articles      | `ORDER BY published_at DESC`                      |
| Filter by category   | JOIN `article_categories`                         |
| Filter by tag        | JOIN `article_tags`                               |
| Filter by location   | JOIN `article_locations`                          |
| Search by keyword    | `ILIKE` on title/body, or add `pg_trgm` GIN index |
| Filter by date range | `WHERE published_at BETWEEN ? AND ?`              |
| Filter by source     | `WHERE source_id = ?`                             |
| Filter by language   | `WHERE language = 'bn'`                           |

### Add `pg_trgm` for fast full-text search

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_articles_title_trgm ON articles USING GIN (title gin_trgm_ops);
CREATE INDEX idx_articles_body_trgm ON articles USING GIN (body gin_trgm_ops);
```

### Improve Bangla keyword matching

If you find that Bangla morphological variants are causing missed matches (e.g. "রাজনৈতিক" vs "রাজনীতি"), you can add more keyword variants to `categories.json` or integrate a Bangla stemmer library such as `bnlp-toolkit`.

### Content-level deduplication (future)

The current dedup is URL-based only. For content-level dedup (syndicated articles), consider:

- Storing a `content_hash` (MD5 of title + first 200 chars of body) in `articles`
- Checking the hash before insert in addition to URL

# Replacement Files — Instructions

These files replace the corresponding files in your repo:
**laksmisanto/F-news-feed-py-scraper**

## What changed and why

| File | Replaces | Why |
|---|---|---|
| `scrapers/article.py` | `scrapers/article.py` | Selector-based extractor replaced with extraction chain: extruct (JSON-LD/OG) → Trafilatura → Newspaper4k → readability-lxml. Works on any news site without per-source config. |
| `processors/category.py` | `processors/category.py` | Adds URL-path parsing + JSON-LD `articleSection` as primary signals. Keyword matching is now supplemental only, prevents noisy false categories. |
| `seed.py` | `seed.py` | 27 confirmed sources with verified RSS URLs. All `html_scrape_config = None` — generic extractor handles everything. |
| `requirements.txt` | `requirements.txt` | Adds `trafilatura`, `newspaper4k`, `extruct`, `readability-lxml`. Keeps `lxml>=5.3.0` for Python 3.12+/3.14 compatibility. |

## Installation steps

```bash
# 1. Pull the latest dependency list
pip install -r requirements.txt

# 2. If Newspaper4k complains on first run, install once:
#    python -c "import nltk; nltk.download('punkt')"
#    (optional — only needed if you use Newspaper4k's NLP features)

# 3. Drop the new files into your repo at the exact paths above.

# 4. Re-run seed to refresh sources + clear stale html_scrape_config:
python seed.py

# 5. Test extraction on one source before running the scheduler:
python main.py --once

# 6. Watch logs/scraper.log — the new extractor logs which extractor
#    won per article (structured / trafilatura / newspaper4k / readability).
#    If a source repeatedly shows "none", investigate that source.

# 7. Start scheduler:
python main.py
```

## Caller integration notes

Your existing `runner.py` calls `scrapers/article.py` with some signature.
The new `extract_article()` function has this signature:

```python
async def extract_article(
    url: str,
    html: str | None = None,
    css_config: dict | None = None,
) -> dict | None:
    """Returns dict with: title, short_description, body, image_url,
       published_at, author, section, extractors_used"""
```

If your `runner.py` currently calls the old function differently (e.g.
`scrape_article(url, source)`), wrap or adapt the call site:

```python
from scrapers.article import extract_article

# Pass source.html_scrape_config (will be None for 95% of sources)
data = await extract_article(url, css_config=source.html_scrape_config)
if data is None:
    # Extraction failed — skip article
    continue

# Map dict keys to your Article ORM fields:
article = Article(
    source_id=source.id,
    url=data["url"],
    title=data["title"],
    short_description=data["short_description"],
    body=data["body"],
    image_url=data["image_url"],
    published_at=data["published_at"],
    scraped_at=datetime.now(timezone.utc),
    language=source.language,
    is_published=True,
)
```

The `extractors_used` field is useful for debugging — log it so you can see
which extractor handled each article in your `fetch_run_logs` table.

## Caller integration for the new category processor

The `process_categories()` function signature also changed slightly:

```python
async def process_categories(
    session,
    article,
    url: str,         # NEW — needed for URL path parsing
    text: str,        # title + description + body
    section: str | None = None,  # NEW — pass data["section"] from extractor
) -> list[Category]:
```

Adapt your caller in `runner.py`:

```python
from processors.category import process_categories

categories = await process_categories(
    session,
    article,
    url=data["url"],
    text=f"{data['title']} {data['short_description'] or ''} {data['body'] or ''}",
    section=data["section"],
)
# Attach to article via article_categories M2M
for cat in categories:
    article.categories.append(cat)
```

## What stays unchanged

Do NOT touch these files — they work fine:

- `main.py`
- `runner.py` (except for the two call-site adapters shown above)
- `scheduler.py`
- `fetchers/` (all files)
- `scrapers/cleaner.py`
- `processors/tag.py`
- `processors/location.py`
- `db/` (all files)
- `utils/`
- `migrations/`
- `config/keywords/*.json` (your existing keyword files keep working as supplemental)

## What to watch in production

1. **Per-source extractor quality** — log `extractors_used` per article. If
   one source always falls back to `readability` instead of `structured`,
   that publisher doesn't emit clean JSON-LD — fine, but expect lower quality.

2. **Body length** — articles with body < 200 chars are suspicious. Add a
   metric in your `fetch_run_logs`.

3. **Image hit rate** — track % of articles that got an image URL. Should be
   >80% for major sources.

4. **Add `html_scrape_config` only when needed** — if after a week one
   source still has bad extraction, manually inspect that site's article
   HTML, write CSS selectors for it, and update its `html_scrape_config`
   in the DB. The chain will pick it up as the final override.

## Important: no Playwright yet

The 4 TV portals (Somoy, Jamuna, Ekattor, DBC) are seeded with
`is_active=False`. These are JS-rendered and need Playwright. Phase 2 work.
Enable them only after adding Playwright to your fetcher chain.
