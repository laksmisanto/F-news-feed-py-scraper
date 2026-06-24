"""
Abstract base fetcher. All fetchers must implement fetch_urls().
"""

from abc import ABC, abstractmethod
from typing import Optional
import httpx

from utils.logger import get_logger

logger = get_logger("fetcher.base")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; NewsScraper/1.0; +https://github.com/news-scraper)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5,bn;q=0.3",
}


class BaseFetcher(ABC):
    name: str = "base"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    async def get(self, url: str, client: httpx.AsyncClient) -> Optional[str]:
        """
        Perform async HTTP GET. Returns response text or None on failure.
        """
        try:
            response = await client.get(url, headers=HEADERS, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
            return response.text
        except httpx.TimeoutException:
            logger.warning(f"[{self.name}] Timeout: {url}")
        except httpx.HTTPStatusError as e:
            logger.warning(f"[{self.name}] HTTP {e.response.status_code}: {url}")
        except Exception as e:
            logger.warning(f"[{self.name}] Error fetching {url}: {e}")
        return None

    @abstractmethod
    async def fetch_urls(
        self,
        source,
        client: httpx.AsyncClient,
        max_articles: int = 10
    ) -> list[str]:
        """
        Return a list of article URLs from the source.
        Must be implemented by each fetcher.
        """
        raise NotImplementedError
