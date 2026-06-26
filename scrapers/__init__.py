
"""
Public exports for the scrapers package.

The article extractor is now a function (extract_article) not a class.
The chain-based design replaced the old ArticleScraper / ArticleData pair.
"""

from scrapers.article import extract_article
from scrapers.cleaner import ArticleCleaner

__all__ = ["extract_article", "ArticleCleaner"]
