"""
General-purpose helper utilities.
"""

import re
import uuid as _uuid
import unicodedata
from datetime import datetime, timezone
from typing import Optional
from dateutil import parser as dateutil_parser


# ---------------------------------------------------------------------------
# SLUGIFY
# Handles both Latin and Bangla text.
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """
    Convert text to a URL-safe slug.
    Bangla text is kept as-is (unicode normalized), spaces → hyphens.
    Latin text is lowercased and stripped of special chars.
    """
    text = str(text).strip()
    text = unicodedata.normalize("NFKC", text)
    # Replace spaces and underscores with hyphens
    text = re.sub(r"[\s_]+", "-", text)
    # Remove characters that are not alphanumeric, hyphens, or unicode letters
    text = re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)
    text = text.lower()
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text or "unnamed"


def generate_slug(title: str) -> str:
    """Unique URL slug with 6-char random suffix to prevent collisions."""
    base = slugify(title)[:150]
    suffix = _uuid.uuid4().hex[:6]
    return f"{base}-{suffix}"


# ---------------------------------------------------------------------------
# DATE PARSING
# ---------------------------------------------------------------------------

def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """
    Parse a date string into a UTC-aware datetime.
    Returns None if parsing fails.
    """
    if not date_str:
        return None
    try:
        dt = dateutil_parser.parse(date_str, fuzzy=True)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def parse_struct_time(struct_time) -> Optional[datetime]:
    """
    Convert feedparser's struct_time (UTC) to a naive UTC datetime.
    """
    try:
        import calendar
        ts = calendar.timegm(struct_time)
        return datetime.utcfromtimestamp(ts)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# TEXT CLEANING
# ---------------------------------------------------------------------------

def clean_text(text: Optional[str]) -> Optional[str]:
    """
    Strip HTML tags, normalize whitespace, return plain text.
    """
    if not text:
        return None
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Remove HTML entities
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def truncate(text: Optional[str], max_len: int = 500) -> Optional[str]:
    """
    Truncate text to max_len characters, breaking at word boundary.
    """
    if not text:
        return None
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "…"


def extract_meta_description(html: str) -> Optional[str]:
    """
    Extract <meta name="description"> or <meta property="og:description"> content.
    """
    patterns = [
        r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+content=["\'](.*?)["\']\s+name=["\']description["\']',
        r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+content=["\'](.*?)["\']\s+property=["\']og:description["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(1))
    return None


def extract_og_image(html: str) -> Optional[str]:
    """
    Extract og:image URL from HTML.
    """
    patterns = [
        r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+content=["\'](.*?)["\']\s+property=["\']og:image["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None
