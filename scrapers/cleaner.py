"""
ArticleCleaner — converts a BeautifulSoup element into clean plain text.
Removes ads, scripts, nav, social widgets, and other noise.
"""

import re
from bs4 import BeautifulSoup, Tag, NavigableString
from typing import Optional


# Tags that should be completely removed with their content
REMOVE_TAGS = {
    "script", "style", "noscript", "iframe", "form",
    "button", "input", "select", "textarea",
    "nav", "header", "footer", "aside",
    "figure",  # we already get image separately
}

# Class/id patterns that indicate noise (ads, social, related)
NOISE_PATTERNS = re.compile(
    r"(ad|ads|advert|advertisement|banner|promo|"
    r"social|share|sharing|related|recommended|"
    r"newsletter|subscribe|comment|sidebar|"
    r"popup|modal|cookie|gdpr|breadcrumb|"
    r"pagination|nav|menu|footer|header)",
    re.IGNORECASE,
)


class ArticleCleaner:

    def clean(self, element: Tag) -> Optional[str]:
        """
        Extract clean plain text from a BeautifulSoup Tag.
        Returns None if the result is empty.
        """
        if element is None:
            return None

        # Work on a copy to avoid mutating the original soup
        el = element.__copy__()

        for tag_name in REMOVE_TAGS:
            for tag in el.find_all(tag_name):
                tag.decompose()

        # Remove elements with noisy class/id attributes
        for tag in el.find_all(True):
            if tag.attrs is None:
                continue
            classes = " ".join(tag.get("class", []))
            tag_id = tag.get("id", "")
            if NOISE_PATTERNS.search(classes) or NOISE_PATTERNS.search(tag_id):
                tag.decompose()

        # Extract text, preserving paragraph breaks
        lines = []
        for child in el.descendants:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    lines.append(text)
            elif isinstance(child, Tag) and child.name in ("p", "br", "h1", "h2", "h3", "h4", "li"):
                lines.append("\n")

        text = " ".join(lines)

        # Clean up whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" \n", "\n", text)
        text = re.sub(r"\n ", "\n", text)
        text = text.strip()

        return text if len(text) > 50 else None
