"""
Category processor — multi-signal category detection.

Priority chain:
  1. URL path parsing  (e.g. /sports/cricket/ → Sports + Cricket)
  2. JSON-LD articleSection / OpenGraph article:section (passed in as `section`)
  3. Keyword matching against categories.json (supplemental, strict)

Why this approach:
  Publishers' own URL taxonomies and structured data are far more reliable
  than guessing categories from article body text. Body matching alone
  produces noise — an ad or "related news" widget mentioning "ক্রিকেট"
  shouldn't make a politics article into a cricket article.
"""

import os
import json
import re
import logging
from urllib.parse import urlparse

from sqlalchemy import select

from db.models import Category

logger = logging.getLogger("processors.category")

CATEGORIES_PATH = os.path.join("config", "keywords", "categories.json")

# Cache for loaded keyword config
_CATEGORIES_CONFIG: dict | None = None


# ---------------------------------------------------------------------------
# URL path → category map
# Common patterns across BD and international news sites.
# Format: { "url_segment": ("Parent Category", "Child Category" or None) }
# ---------------------------------------------------------------------------
URL_PATH_MAP = {
    # ── Sports ──
    "sports": ("Sports", None),
    "sport": ("Sports", None),
    "খেলা": ("Sports", None),
    "khela": ("Sports", None),
    "cricket": ("Sports", "Cricket"),
    "ক্রিকেট": ("Sports", "Cricket"),
    "football": ("Sports", "Football"),
    "ফুটবল": ("Sports", "Football"),
    "tennis": ("Sports", "Tennis"),

    # ── Politics ──
    "politics": ("Politics", None),
    "political": ("Politics", None),
    "রাজনীতি": ("Politics", None),
    "rajniti": ("Politics", None),

    # ── Business / Economy ──
    "business": ("Business", None),
    "economy": ("Business", "Economy"),
    "economic": ("Business", "Economy"),
    "finance": ("Business", "Finance"),
    "markets": ("Business", "Markets"),
    "stocks": ("Business", "Markets"),
    "অর্থনীতি": ("Business", "Economy"),
    "বাণিজ্য": ("Business", None),
    "banijjo": ("Business", None),

    # ── International / World ──
    "international": ("International", None),
    "world": ("International", None),
    "global": ("International", None),
    "আন্তর্জাতিক": ("International", None),
    "antorjatik": ("International", None),
    "foreign": ("International", None),

    # ── National / Bangladesh ──
    "bangladesh": ("Bangladesh", None),
    "national": ("Bangladesh", None),
    "জাতীয়": ("Bangladesh", None),
    "jatiyo": ("Bangladesh", None),
    "country": ("Bangladesh", None),

    # ── Technology ──
    "technology": ("Technology", None),
    "tech": ("Technology", None),
    "gadgets": ("Technology", "Gadgets"),
    "ai": ("Technology", "AI"),
    "প্রযুক্তি": ("Technology", None),
    "projukti": ("Technology", None),
    "tech-and-gadget": ("Technology", None),

    # ── Entertainment ──
    "entertainment": ("Entertainment", None),
    "bollywood": ("Entertainment", None),
    "dhallywood": ("Entertainment", None),
    "hollywood": ("Entertainment", None),
    "music": ("Entertainment", "Music"),
    "cinema": ("Entertainment", "Movies"),
    "movies": ("Entertainment", "Movies"),
    "বিনোদন": ("Entertainment", None),
    "binodon": ("Entertainment", None),

    # ── Lifestyle ──
    "lifestyle": ("Lifestyle", None),
    "life-style": ("Lifestyle", None),
    "fashion": ("Lifestyle", "Fashion"),
    "food": ("Lifestyle", "Food"),
    "travel": ("Lifestyle", "Travel"),
    "জীবনযাপন": ("Lifestyle", None),

    # ── Health ──
    "health": ("Health", None),
    "স্বাস্থ্য": ("Health", None),
    "shasthya": ("Health", None),
    "medicine": ("Health", None),

    # ── Education ──
    "education": ("Education", None),
    "শিক্ষা": ("Education", None),
    "shikkha": ("Education", None),
    "campus": ("Education", "Campus"),

    # ── Opinion / Editorial ──
    "opinion": ("Opinion", None),
    "editorial": ("Opinion", "Editorial"),
    "column": ("Opinion", "Column"),
    "মতামত": ("Opinion", None),
    "motamot": ("Opinion", None),

    # ── Crime ──
    "crime": ("Crime", None),
    "অপরাধ": ("Crime", None),

    # ── Science / Environment ──
    "science": ("Science", None),
    "environment": ("Environment", None),
    "climate": ("Environment", "Climate"),
    "বিজ্ঞান": ("Science", None),

    # ── Religion ──
    "religion": ("Religion", None),
    "islam": ("Religion", "Islam"),
    "ধর্ম": ("Religion", None),
}


# ---------------------------------------------------------------------------
# Section name → category map (for JSON-LD articleSection / og:section)
# ---------------------------------------------------------------------------
SECTION_MAP = {
    # English
    "sports": ("Sports", None),
    "cricket": ("Sports", "Cricket"),
    "football": ("Sports", "Football"),
    "politics": ("Politics", None),
    "business": ("Business", None),
    "economy": ("Business", "Economy"),
    "world": ("International", None),
    "international": ("International", None),
    "bangladesh": ("Bangladesh", None),
    "national": ("Bangladesh", None),
    "technology": ("Technology", None),
    "tech": ("Technology", None),
    "entertainment": ("Entertainment", None),
    "lifestyle": ("Lifestyle", None),
    "health": ("Health", None),
    "education": ("Education", None),
    "opinion": ("Opinion", None),
    "editorial": ("Opinion", "Editorial"),
    "crime": ("Crime", None),
    "science": ("Science", None),
    "environment": ("Environment", None),
    # Bangla
    "খেলা": ("Sports", None),
    "ক্রিকেট": ("Sports", "Cricket"),
    "রাজনীতি": ("Politics", None),
    "অর্থনীতি": ("Business", "Economy"),
    "বাণিজ্য": ("Business", None),
    "আন্তর্জাতিক": ("International", None),
    "জাতীয়": ("Bangladesh", None),
    "প্রযুক্তি": ("Technology", None),
    "বিনোদন": ("Entertainment", None),
    "স্বাস্থ্য": ("Health", None),
    "শিক্ষা": ("Education", None),
    "মতামত": ("Opinion", None),
}


def _load_categories_config() -> dict:
    """Lazy-load categories.json once and cache."""
    global _CATEGORIES_CONFIG
    if _CATEGORIES_CONFIG is not None:
        return _CATEGORIES_CONFIG
    try:
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            _CATEGORIES_CONFIG = json.load(f)
    except Exception as e:
        logger.warning(f"[category] Failed to load categories.json: {e}")
        _CATEGORIES_CONFIG = {}
    return _CATEGORIES_CONFIG


# ---------------------------------------------------------------------------
# Signal 1: URL path parsing
# ---------------------------------------------------------------------------
def detect_from_url(url: str) -> list[tuple[str, str | None]]:
    """
    Extract category from URL path segments.

    Examples:
      https://prothomalo.com/sports/cricket/article → [("Sports", "Cricket")]
      https://thedailystar.net/business/economy/x  → [("Business", "Economy")]
    """
    if not url:
        return []

    try:
        parsed = urlparse(url)
        # Lowercase + split path
        segments = [s.lower() for s in parsed.path.split("/") if s]
    except Exception:
        return []

    detected = []
    for seg in segments:
        # Try direct match
        if seg in URL_PATH_MAP:
            detected.append(URL_PATH_MAP[seg])
            continue
        # Try without trailing punctuation/numbers
        cleaned = re.sub(r"[-_].*$", "", seg)
        if cleaned in URL_PATH_MAP:
            detected.append(URL_PATH_MAP[cleaned])

    return detected


# ---------------------------------------------------------------------------
# Signal 2: Section from structured data
# ---------------------------------------------------------------------------
def detect_from_section(section: str | None) -> list[tuple[str, str | None]]:
    """Map JSON-LD articleSection or og:section to category tuple."""
    if not section or not isinstance(section, str):
        return []

    key = section.lower().strip()
    if key in SECTION_MAP:
        return [SECTION_MAP[key]]

    # Try first word
    first_word = key.split()[0] if key else ""
    if first_word in SECTION_MAP:
        return [SECTION_MAP[first_word]]

    return []


# ---------------------------------------------------------------------------
# Signal 3: Keyword matching (supplemental, strict)
# ---------------------------------------------------------------------------
def detect_from_keywords(text: str) -> list[tuple[str, str | None]]:
    """
    Match category keywords in article text.
    Only used as supplemental signal — requires multiple matches to count.
    """
    if not text or len(text) < 50:
        return []

    cfg = _load_categories_config()
    if not cfg:
        return []

    text_lower = text.lower()
    detected = []

    for parent_name, parent_data in cfg.items():
        parent_keywords = parent_data.get("keywords", [])
        # Count parent matches
        parent_hits = sum(
            1 for kw in parent_keywords
            if kw.lower() in text_lower
        )

        # Check children first (more specific)
        children = parent_data.get("children", {})
        child_matched = False
        for child_name, child_keywords in children.items():
            child_hits = sum(
                1 for kw in child_keywords
                if kw.lower() in text_lower
            )
            # Require at least 2 keyword hits to consider it a real match
            if child_hits >= 2:
                detected.append((parent_name, child_name))
                child_matched = True
                break  # one child per parent

        # Parent-only if no child matched but parent has multiple hits
        if not child_matched and parent_hits >= 3:
            detected.append((parent_name, None))

    return detected


# ---------------------------------------------------------------------------
# Main entry: combine all signals and persist categories
# ---------------------------------------------------------------------------
async def process_categories(
    session,
    article,
    url: str,
    text: str,
    section: str | None = None,
) -> list[Category]:
    """
    Run all signals, deduplicate, and return Category ORM objects.
    Caller is responsible for attaching them to article via article_categories.

    Args:
        session: SQLAlchemy AsyncSession
        article: Article ORM (only used for logging)
        url: Article URL — for URL path detection
        text: title + description + body for keyword detection (supplemental)
        section: structured-data section if available
    """
    # Run all three signals
    sig_url = detect_from_url(url)
    sig_section = detect_from_section(section)
    sig_keywords = detect_from_keywords(text) if (not sig_url and not sig_section) else []
    # Note: only fall back to keywords if structured signals returned nothing.
    # This prevents keyword noise overriding clean URL/JSON-LD signals.

    # Combine in priority order, dedupe by (parent, child) tuple
    combined: list[tuple[str, str | None]] = []
    seen = set()
    for sig in (sig_url, sig_section, sig_keywords):
        for tup in sig:
            if tup not in seen:
                seen.add(tup)
                combined.append(tup)

    if not combined:
        return []

    # Convert to Category ORM rows (create-if-missing)
    categories: list[Category] = []
    for parent_name, child_name in combined:
        parent = await _get_or_create_category(session, parent_name, parent_id=None)
        categories.append(parent)

        if child_name:
            child = await _get_or_create_category(
                session, child_name, parent_id=parent.id
            )
            categories.append(child)

    return categories


async def _get_or_create_category(
    session,
    name: str,
    parent_id: int | None,
) -> Category:
    """Find category by name+parent, or create it."""
    result = await session.execute(
        select(Category).where(
            Category.name == name,
            Category.parent_id == parent_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    slug = _slugify(name)
    cat = Category(name=name, slug=slug, parent_id=parent_id)
    session.add(cat)
    await session.flush()
    return cat


def _slugify(text: str) -> str:
    """Simple slugify — keep alphanumerics and dashes, lowercase."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "-", text)
    return text or "category"
