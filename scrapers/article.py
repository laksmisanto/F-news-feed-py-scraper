"""
Article extractor — extraction chain pattern.

Strategy (in order, first valid wins):
  1. extruct → JSON-LD (NewsArticle schema), OpenGraph, Microdata, Twitter Card
  2. Trafilatura → body extraction (best general extractor)
  3. Newspaper4k → fallback with built-in NLP
  4. readability-lxml → last resort
  5. Optional per-source CSS override from html_scrape_config (rare)

Why this works on "any news site without per-site selectors":
- Modern news publishers add schema.org NewsArticle JSON-LD for Google News
  indexing. This single source gives title, image, body, date, author cleanly.
- Trafilatura uses ML/heuristics — no selectors needed, works on Bangla and English.
- Image extraction has its own priority chain: JSON-LD → og:image →
  twitter:image → body <img> → largest page image.
- Body cleanup strips ads, social widgets, related-news blocks before storage.
"""

import re
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
import extruct
import trafilatura
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from readability import Document as ReadabilityDocument

# Newspaper4k is heavy — import lazily to keep startup fast
_newspaper4k = None


def _get_newspaper4k():
    global _newspaper4k
    if _newspaper4k is None:
        try:
            from newspaper import Article as NPArticle
            _newspaper4k = NPArticle
        except ImportError:
            _newspaper4k = False  # mark as unavailable
    return _newspaper4k if _newspaper4k is not False else None


logger = logging.getLogger("scrapers.article")


# ---------------------------------------------------------------------------
# Common HTTP headers — look like a real browser
# ---------------------------------------------------------------------------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,bn;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


# ---------------------------------------------------------------------------
# Noise selectors — stripped from body before extraction
# ---------------------------------------------------------------------------
NOISE_SELECTORS = [
    "script", "style", "noscript", "iframe", "svg",
    "nav", "aside", "header", "footer",
    ".ad", ".ads", ".advertisement", ".ad-wrapper", ".ad-container",
    ".sponsored", ".promo", ".promotion",
    ".social-share", ".share-buttons", ".sharing", ".social",
    ".related-news", ".related-articles", ".related-posts", ".related",
    ".recommended", ".more-news", ".also-read",
    ".comments", ".comment-section", ".disqus",
    ".newsletter", ".subscribe",
    ".breadcrumb", ".breadcrumbs",
    ".tags", ".article-tags",
    ".author-bio", ".author-card",
    ".popup", ".modal", ".overlay",
    "[class*='ad-']", "[id*='ad-']",
    "[class*='banner']",
]


def _strip_noise(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove common noise elements before extraction."""
    for sel in NOISE_SELECTORS:
        try:
            for el in soup.select(sel):
                el.decompose()
        except Exception:
            continue
    return soup


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------
async def fetch_html(url: str, timeout: int = 30) -> str | None:
    """Fetch HTML with redirect following and proper headers."""
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except Exception as e:
        logger.warning(f"[fetch] {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Structured data: JSON-LD / OpenGraph / Microdata / Twitter Card
# ---------------------------------------------------------------------------
def extract_structured(html: str, url: str) -> dict:
    """
    Pull structured metadata using extruct.
    Returns dict with: title, description, body, image, published_at, author, section
    """
    out = {
        "title": None,
        "description": None,
        "body": None,
        "image": None,
        "published_at": None,
        "author": None,
        "section": None,
    }

    try:
        data = extruct.extract(
            html,
            base_url=url,
            syntaxes=["json-ld", "opengraph", "microdata", "rdfa"],
            uniform=True,
        )
    except Exception as e:
        logger.debug(f"[extruct] {url}: {e}")
        return out

    # 1) JSON-LD NewsArticle / Article — highest priority
    for item in data.get("json-ld", []) or []:
        item_type = item.get("@type", "")
        if isinstance(item_type, list):
            item_type = " ".join(item_type)
        if not re.search(r"(News)?Article|BlogPosting|Report", str(item_type), re.I):
            continue

        out["title"] = out["title"] or item.get("headline") or item.get("name")
        out["description"] = out["description"] or item.get("description")
        out["body"] = out["body"] or item.get("articleBody")
        out["section"] = out["section"] or item.get("articleSection")

        # Image: can be str, dict, or list
        img = item.get("image")
        if img and not out["image"]:
            if isinstance(img, list) and img:
                img = img[0]
            if isinstance(img, dict):
                img = img.get("url") or img.get("@id")
            if isinstance(img, str):
                out["image"] = img

        # Date
        date_val = (
            item.get("datePublished")
            or item.get("dateCreated")
            or item.get("dateModified")
        )
        if date_val and not out["published_at"]:
            out["published_at"] = _parse_date(date_val)

        # Author
        author = item.get("author")
        if author and not out["author"]:
            if isinstance(author, list) and author:
                author = author[0]
            if isinstance(author, dict):
                author = author.get("name")
            if isinstance(author, str):
                out["author"] = author

        # Section: can be list
        if isinstance(out["section"], list):
            out["section"] = out["section"][0] if out["section"] else None

    # 2) OpenGraph — fill anything still missing
    og = data.get("opengraph", [])
    if og:
        og_item = og[0] if isinstance(og, list) else og
        if isinstance(og_item, dict):
            out["title"] = out["title"] or og_item.get("og:title")
            out["description"] = out["description"] or og_item.get("og:description")
            out["image"] = out["image"] or og_item.get("og:image")
            out["section"] = out["section"] or og_item.get("article:section")
            if not out["published_at"]:
                pt = og_item.get("article:published_time")
                if pt:
                    out["published_at"] = _parse_date(pt)
            if not out["author"]:
                out["author"] = og_item.get("article:author")

    # 3) Microdata fallback
    for item in data.get("microdata", []) or []:
        props = item.get("properties", {}) if isinstance(item, dict) else {}
        out["title"] = out["title"] or props.get("headline") or props.get("name")
        out["description"] = out["description"] or props.get("description")
        img = props.get("image")
        if img and not out["image"]:
            if isinstance(img, dict):
                img = img.get("url")
            if isinstance(img, str):
                out["image"] = img
        if not out["published_at"]:
            dp = props.get("datePublished")
            if dp:
                out["published_at"] = _parse_date(dp)

    # Resolve relative image URL
    if out["image"]:
        out["image"] = urljoin(url, out["image"])

    return out


# ---------------------------------------------------------------------------
# Date parsing — tolerant to many formats
# ---------------------------------------------------------------------------
def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, list) and value:
        value = value[0]
    if not isinstance(value, str):
        return None
    try:
        dt = date_parser.parse(value)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Trafilatura body extraction
# ---------------------------------------------------------------------------
def extract_with_trafilatura(html: str, url: str) -> str | None:
    """Use Trafilatura to get clean article body text."""
    try:
        body = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=False,
            favor_recall=True,
            deduplicate=True,
            output_format="txt",
        )
        if body and len(body.strip()) >= 100:
            return body.strip()
    except Exception as e:
        logger.debug(f"[trafilatura] {url}: {e}")
    return None


def extract_trafilatura_metadata(html: str, url: str) -> dict:
    """Trafilatura can also pull metadata — used as a fallback."""
    out = {"title": None, "image": None, "published_at": None, "author": None}
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        if meta:
            out["title"] = getattr(meta, "title", None)
            out["image"] = getattr(meta, "image", None)
            out["author"] = getattr(meta, "author", None)
            d = getattr(meta, "date", None)
            if d:
                out["published_at"] = _parse_date(d)
    except Exception as e:
        logger.debug(f"[trafilatura-meta] {url}: {e}")
    return out


# ---------------------------------------------------------------------------
# Newspaper4k fallback
# ---------------------------------------------------------------------------
def extract_with_newspaper(html: str, url: str) -> dict:
    """Newspaper4k as secondary extractor."""
    out = {"title": None, "body": None, "image": None, "published_at": None}
    NPArticle = _get_newspaper4k()
    if NPArticle is None:
        return out
    try:
        art = NPArticle(url=url, language="en")
        art.download(input_html=html)
        art.parse()
        out["title"] = art.title or None
        out["body"] = art.text.strip() if art.text else None
        out["image"] = art.top_image or None
        if art.publish_date:
            d = art.publish_date
            out["published_at"] = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception as e:
        logger.debug(f"[newspaper4k] {url}: {e}")
    return out


# ---------------------------------------------------------------------------
# readability-lxml — last-resort body extractor
# ---------------------------------------------------------------------------
def extract_with_readability(html: str) -> dict:
    """Mozilla Reader Mode port. Returns title + cleaned HTML body."""
    out = {"title": None, "body": None}
    try:
        doc = ReadabilityDocument(html)
        out["title"] = doc.short_title() or None
        body_html = doc.summary(html_partial=True) or ""
        if body_html:
            soup = BeautifulSoup(body_html, "lxml")
            text = soup.get_text(separator="\n", strip=True)
            if text and len(text) >= 100:
                out["body"] = text
    except Exception as e:
        logger.debug(f"[readability] {e}")
    return out


# ---------------------------------------------------------------------------
# Optional CSS-selector override (rare — only when generic chain fails)
# ---------------------------------------------------------------------------
def extract_with_css_config(html: str, url: str, cfg: dict) -> dict:
    """Per-source CSS selector extraction. Used only when explicitly configured."""
    out = {"title": None, "body": None, "image": None, "published_at": None}
    if not cfg:
        return out
    try:
        soup = BeautifulSoup(html, "lxml")
        soup = _strip_noise(soup)

        if cfg.get("title"):
            el = soup.select_one(cfg["title"])
            if el:
                out["title"] = el.get_text(strip=True) or None

        if cfg.get("body"):
            el = soup.select_one(cfg["body"])
            if el:
                text = el.get_text(separator="\n", strip=True)
                if text and len(text) >= 100:
                    out["body"] = text

        if cfg.get("image"):
            el = soup.select_one(cfg["image"])
            if el:
                src = (
                    el.get("data-src")
                    or el.get("data-lazy-src")
                    or el.get("data-original")
                    or el.get("src")
                )
                if src:
                    out["image"] = urljoin(url, src)

        if cfg.get("date"):
            el = soup.select_one(cfg["date"])
            if el:
                date_str = (
                    el.get("datetime")
                    or el.get("content")
                    or el.get_text(strip=True)
                )
                if date_str:
                    out["published_at"] = _parse_date(date_str)
    except Exception as e:
        logger.debug(f"[css-config] {url}: {e}")
    return out


# ---------------------------------------------------------------------------
# Image priority chain
# ---------------------------------------------------------------------------
def pick_best_image(
    structured_image: str | None,
    html: str,
    url: str,
    body_text: str | None = None,
) -> str | None:
    """
    Image priority:
      1. JSON-LD / OG image (already in structured_image)
      2. twitter:image meta
      3. First <img> inside article body
      4. Largest <img> on page above size threshold
    All filtered for tracking pixels and data URIs.
    """
    if structured_image and _is_valid_image_url(structured_image):
        return structured_image

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None

    # twitter:image
    tw = soup.find("meta", attrs={"name": "twitter:image"})
    if tw and tw.get("content"):
        candidate = urljoin(url, tw["content"])
        if _is_valid_image_url(candidate):
            return candidate

    tw_prop = soup.find("meta", attrs={"property": "twitter:image"})
    if tw_prop and tw_prop.get("content"):
        candidate = urljoin(url, tw_prop["content"])
        if _is_valid_image_url(candidate):
            return candidate

    # First img inside likely article container
    article_container = (
        soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find("main")
    )
    container = article_container or soup
    for img in container.find_all("img"):
        src = (
            img.get("data-src")
            or img.get("data-lazy-src")
            or img.get("data-original")
            or img.get("src")
        )
        if not src:
            continue
        candidate = urljoin(url, src)
        if _is_valid_image_url(candidate):
            # quick size hint check
            w = img.get("width") or 0
            h = img.get("height") or 0
            try:
                w, h = int(w), int(h)
                if w and h and (w < 100 or h < 100):
                    continue
            except (ValueError, TypeError):
                pass
            return candidate

    return None


def _is_valid_image_url(url: str) -> bool:
    """Reject tracking pixels, data URIs, and obvious garbage."""
    if not url or not isinstance(url, str):
        return False
    if url.startswith("data:"):
        return False
    lower = url.lower()
    # Reject 1x1 tracking pixels (heuristic)
    if any(s in lower for s in ["1x1", "pixel.gif", "transparent.gif", "spacer.gif"]):
        return False
    if not lower.startswith(("http://", "https://", "//")):
        return False
    return True


# ---------------------------------------------------------------------------
# Body cleanup
# ---------------------------------------------------------------------------
def clean_body_text(text: str) -> str:
    """Normalize whitespace and strip remaining junk."""
    if not text:
        return ""
    # Collapse multiple newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Strip leading/trailing whitespace per line
    text = "\n".join(line.strip() for line in text.splitlines())
    # Remove common junk phrases
    junk_patterns = [
        r"^\s*Share this article\s*$",
        r"^\s*Read more:.*$",
        r"^\s*Also read:.*$",
        r"^\s*Subscribe to our newsletter.*$",
        r"^\s*Click here to.*$",
    ]
    for pat in junk_patterns:
        text = re.sub(pat, "", text, flags=re.MULTILINE | re.IGNORECASE)
    return text.strip()


# ---------------------------------------------------------------------------
# Main entry point — the extraction chain
# ---------------------------------------------------------------------------
async def extract_article(
    url: str,
    html: str | None = None,
    css_config: dict | None = None,
) -> dict | None:
    """
    Run the full extraction chain.

    Args:
        url: Article URL.
        html: Pre-fetched HTML. If None, will be fetched.
        css_config: Optional per-source CSS override (rare).

    Returns:
        dict with: title, short_description, body, image_url, published_at,
                   author, section, extractor_used
        or None if extraction failed entirely.
    """
    if html is None:
        html = await fetch_html(url)
        if not html:
            return None

    # Track which extractors contributed
    extractors_used = []

    # === STEP 1: Structured data (JSON-LD, OG, Microdata) ===
    structured = extract_structured(html, url)
    if any(structured.values()):
        extractors_used.append("structured")

    # === STEP 2: Trafilatura body + metadata ===
    traf_body = extract_with_trafilatura(html, url)
    traf_meta = extract_trafilatura_metadata(html, url)
    if traf_body:
        extractors_used.append("trafilatura")

    # === STEP 3: Newspaper4k (only if body still missing) ===
    np_data = {}
    if not traf_body and not structured.get("body"):
        np_data = extract_with_newspaper(html, url)
        if np_data.get("body"):
            extractors_used.append("newspaper4k")

    # === STEP 4: readability (last resort) ===
    read_data = {}
    if (
        not traf_body
        and not structured.get("body")
        and not np_data.get("body")
    ):
        read_data = extract_with_readability(html)
        if read_data.get("body"):
            extractors_used.append("readability")

    # === STEP 5: Per-source CSS override (only if explicitly configured) ===
    css_data = {}
    if css_config:
        css_data = extract_with_css_config(html, url, css_config)
        if css_data.get("body") or css_data.get("title"):
            extractors_used.append("css_config")

    # ── Merge results in priority order ──────────────────────────────────────
    title = (
        css_data.get("title")
        or structured.get("title")
        or traf_meta.get("title")
        or np_data.get("title")
        or read_data.get("title")
    )

    body = (
        structured.get("body")
        or traf_body
        or np_data.get("body")
        or read_data.get("body")
        or css_data.get("body")
    )

    image = (
        css_data.get("image")
        or structured.get("image")
        or traf_meta.get("image")
        or np_data.get("image")
    )
    # Fallback to image picker if still missing
    if not image:
        image = pick_best_image(None, html, url, body)

    published_at = (
        structured.get("published_at")
        or css_data.get("published_at")
        or traf_meta.get("published_at")
        or np_data.get("published_at")
    )

    author = structured.get("author") or traf_meta.get("author")
    section = structured.get("section")
    short_description = structured.get("description")

    # ── Quality gate ─────────────────────────────────────────────────────────
    if not title:
        logger.info(f"[extract] No title found, skipping: {url}")
        return None

    if body:
        body = clean_body_text(body)

    if not short_description and body:
        # Use first 250 chars of body as description fallback
        short_description = body[:250].rsplit(" ", 1)[0] + "..."

    return {
        "url": url,
        "title": title.strip() if title else None,
        "short_description": short_description.strip() if short_description else None,
        "body": body or None,
        "image_url": image,
        "published_at": published_at,
        "author": author.strip() if author else None,
        "section": section.strip() if isinstance(section, str) else None,
        "extractors_used": ",".join(extractors_used) if extractors_used else "none",
    }
