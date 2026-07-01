"""
Tag processor — extracts dynamic tags from article text.

Strategy:
  1. Extract named entities (person names, org names) using regex patterns
  2. Extract Bangla proper nouns using capitalization/pattern heuristics
  3. Extract meaningful English noun phrases
  4. Clean and deduplicate
  5. Get or create tag in DB
"""

import re
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from db.queries import get_or_create_tag
from utils.logger import get_logger

logger = get_logger("processor.tag")

MAX_TAGS_PER_ARTICLE = 10
MIN_TAG_LENGTH = 3
MAX_TAG_LENGTH = 60

# Patterns for English named entities (proper nouns, organizations, etc.)
ENGLISH_ENTITY_PATTERNS = [
    # Quoted phrases (often titles or named entities)
    r'"([A-Z][^"]{2,40})"',
    # ALL CAPS acronyms (min 2 chars)
    r'\b([A-Z]{2,8})\b',
    # CamelCase or Title Case multi-word (e.g., "Prime Minister", "Sheikh Hasina")
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b',
]

# Bangla proper noun patterns
BANGLA_NOUN_PATTERNS = [
    # Words after common Bangla title markers
    r'(?:মন্ত্রী|প্রধানমন্ত্রী|রাষ্ট্রপতি|চেয়ারম্যান|সভাপতি|সাংসদ|এমপি)\s+([\u0980-\u09FF]+(?:\s+[\u0980-\u09FF]+){0,2})',
    # Quoted Bangla phrases
    r'[""]([\u0980-\u09FF\s]{4,30})["""]',
]

# Words to always exclude from tags (common noise)
STOPWORDS_EN = {
    "the", "this", "that", "these", "those", "with", "from", "have", "been",
    "will", "would", "could", "should", "said", "says", "also", "more",
    "new", "one", "two", "all", "its", "his", "her", "their", "our",
    "not", "but", "for", "are", "was", "has", "had", "they", "them",
    "after", "before", "about", "over", "under", "into", "out", "up",
    "NEWS", "PHOTO", "FILE", "REPORT", "UPDATE", "READ", "WATCH", "LIVE",
}


class TagProcessor:

    def extract(self, text: str, language: str = "en") -> list[str]:
        """
        Extract candidate tags from article text.
        Returns list of clean tag strings.
        """
        if not text:
            return []

        tags = set()

        if language == "en":
            tags.update(self._extract_english(text))
        elif language == "bn":
            tags.update(self._extract_bangla(text))
            # Also pick up English acronyms/names in Bangla articles
            tags.update(self._extract_acronyms(text))

        # Clean and filter
        cleaned = []
        for tag in tags:
            tag = tag.strip()
            if len(tag) < MIN_TAG_LENGTH or len(tag) > MAX_TAG_LENGTH:
                continue
            if tag.upper() in STOPWORDS_EN or tag.lower() in STOPWORDS_EN:
                continue
            cleaned.append(tag)

        # Deduplicate (case-insensitive for English)
        seen = set()
        result = []
        for tag in cleaned:
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                result.append(tag)

        return result[:MAX_TAGS_PER_ARTICLE]

    def _extract_english(self, text: str) -> set[str]:
        tags = set()
        for pattern in ENGLISH_ENTITY_PATTERNS:
            for match in re.finditer(pattern, text):
                candidate = match.group(1).strip()
                if candidate and candidate.upper() not in STOPWORDS_EN:
                    tags.add(candidate)
        return tags

    def _extract_bangla(self, text: str) -> set[str]:
        tags = set()
        for pattern in BANGLA_NOUN_PATTERNS:
            for match in re.finditer(pattern, text):
                candidate = match.group(1).strip()
                if candidate and len(candidate) >= MIN_TAG_LENGTH:
                    tags.add(candidate)
        return tags

    def _extract_acronyms(self, text: str) -> set[str]:
        """Pick up ALL CAPS acronyms from any language text."""
        return {m.group(0) for m in re.finditer(r'\b[A-Z]{2,8}\b', text)
                if m.group(0) not in STOPWORDS_EN}

    async def resolve(
        self, text: str, language: str, session: AsyncSession
    ) -> list[int]:
        """
        Extract tags and resolve to DB IDs (get or create).
        """
        tag_names = self.extract(text, language)
        if not tag_names:
            return []

        tag_ids = []
        for name in tag_names:
            try:
                tag = await get_or_create_tag(session, name)
                if tag is not None:
                    tag_ids.append(tag.id)
            except Exception as e:
                logger.warning(f"[Tag] DB error for '{name}': {e}")

        return tag_ids
