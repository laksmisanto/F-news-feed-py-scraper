"""
Category processor — keyword-based matching.
Resolves parent + child categories from article text.

Logic:
  1. Match child keywords first (most specific)
  2. If child found → assign child + its parent
  3. If only parent keywords match → assign parent only
  4. Multiple categories per article are allowed
"""

import json
import re
import os
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from db.queries import get_or_create_category
from utils.logger import get_logger

logger = get_logger("processor.category")

KEYWORDS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "keywords", "categories.json"
)


class CategoryProcessor:

    def __init__(self):
        self._data: dict = {}
        self._load()

    def _load(self):
        try:
            with open(KEYWORDS_PATH, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            logger.info(f"[Category] Loaded {len(self._data)} parent categories")
        except Exception as e:
            logger.error(f"[Category] Failed to load categories.json: {e}")
            self._data = {}

    def _matches(self, text: str, keywords: list[str]) -> bool:
        """Case-insensitive keyword search in text."""
        text_lower = text.lower()
        for kw in keywords:
            if kw.lower() in text_lower:
                return True
        return False

    def detect(self, text: str) -> list[dict]:
        """
        Detect matching categories from text.
        Returns list of dicts: {parent_name, child_name (optional)}
        """
        if not text or not self._data:
            return []

        matches = []
        seen_parents = set()

        for parent_name, parent_data in self._data.items():
            children = parent_data.get("children", {})

            # Try children first (most specific)
            child_matched = False
            for child_name, child_keywords in children.items():
                if self._matches(text, child_keywords):
                    matches.append({
                        "parent_name": parent_name,
                        "child_name": child_name,
                    })
                    seen_parents.add(parent_name)
                    child_matched = True

            # If no child matched, try parent keywords
            if not child_matched and parent_name not in seen_parents:
                if self._matches(text, parent_data.get("keywords", [])):
                    matches.append({
                        "parent_name": parent_name,
                        "child_name": None,
                    })

        return matches

    async def resolve(
        self, text: str, session: AsyncSession
    ) -> list[int]:
        """
        Run detect() and resolve each match to DB category IDs.
        Creates categories on the fly if they don't exist.
        Returns list of category IDs to attach to the article.
        """
        matches = self.detect(text)
        if not matches:
            return []

        category_ids = []

        for match in matches:
            parent_name = match["parent_name"]
            child_name = match.get("child_name")

            try:
                # Get or create parent
                parent = await get_or_create_category(session, parent_name, parent_id=None)

                if child_name:
                    # Get or create child, linked to parent
                    child = await get_or_create_category(session, child_name, parent_id=parent.id)
                    if parent.id not in category_ids:
                        category_ids.append(parent.id)
                    if child.id not in category_ids:
                        category_ids.append(child.id)
                else:
                    if parent.id not in category_ids:
                        category_ids.append(parent.id)

            except Exception as e:
                logger.warning(f"[Category] DB error for '{parent_name}': {e}")

        return category_ids
