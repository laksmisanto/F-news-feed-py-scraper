"""
Shared Playwright browser manager.

One Chromium instance per scraper run. Each source opens its own context
(isolated cookies/storage) — cheap. Pages are created and closed per render.

Memory footprint:
    Browser:  ~200 MB resident
    Context:  +5-10 MB per active context
    Page:     +10-20 MB while open (freed on close)

Usage:
    async with PlaywrightManager() as pw:
        async with pw.context() as ctx:
            html = await ctx.render("https://example.com/article")
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from utils.logger import get_logger

logger = get_logger("browser")


# Realistic Chrome on Linux — same as fetchers/base.py for consistency
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Default viewport — keep matching a real desktop browser
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}

# Wait strategies in increasing strictness
VALID_WAIT_STATES = ("load", "domcontentloaded", "networkidle", "commit")


class PlaywrightManager:
    """
    Async context manager wrapping a single Chromium browser instance.

    The browser is launched in __aenter__ and closed in __aexit__.
    Use .context() to get an isolated browsing context (one per source
    is the recommended pattern).
    """

    def __init__(self, headless: bool = True, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo  # ms to wait between actions; 0 in prod
        self._pw = None
        self._browser = None

    async def __aenter__(self):
        # Lazy import — playwright is optional. If it's not installed,
        # PlaywrightManager should never be instantiated.
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
        except Exception as e:
            await self._pw.stop()
            logger.error(f"[Playwright] Browser launch failed: {e}")
            raise

        logger.info("[Playwright] Browser launched")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._browser:
                await self._browser.close()
        except Exception as e:
            logger.warning(f"[Playwright] Browser close error: {e}")
        try:
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            logger.warning(f"[Playwright] Stop error: {e}")
        logger.info("[Playwright] Browser closed")

    @asynccontextmanager
    async def context(
        self,
        user_agent: Optional[str] = None,
        locale: str = "en-US",
        extra_http_headers: Optional[dict] = None,
    ):
        """
        Yield an isolated BrowserContext.
        Closes the underlying Playwright context on exit.
        """
        if self._browser is None:
            raise RuntimeError(
                "PlaywrightManager not started. Use 'async with PlaywrightManager()'"
            )

        pw_context = await self._browser.new_context(
            user_agent=user_agent or DEFAULT_UA,
            locale=locale,
            viewport=DEFAULT_VIEWPORT,
            ignore_https_errors=True,
            extra_http_headers=extra_http_headers or {},
        )

        # Block heavy resources we don't need for HTML extraction
        # (images, fonts, media). This cuts page load time 30-50% and
        # is safe because Trafilatura/extruct work on the DOM, not images.
        async def _block_heavy(route, request):
            if request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()

        await pw_context.route("**/*", _block_heavy)

        try:
            yield BrowserContext(pw_context)
        finally:
            try:
                await pw_context.close()
            except Exception as e:
                logger.debug(f"[Playwright] Context close error: {e}")


class BrowserContext:
    """
    Thin wrapper around a Playwright BrowserContext.
    Exposes a single render() method for fetching rendered HTML.
    """

    def __init__(self, pw_context):
        self._ctx = pw_context

    async def render(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout: int = 30000,
    ) -> Optional[str]:
        """
        Navigate to url, wait per wait_until, return final HTML.

        Args:
            url: target URL
            wait_until: 'load', 'domcontentloaded', 'networkidle', or 'commit'
            timeout: max time to wait, in milliseconds (Playwright units)

        Returns:
            HTML string on success, None on failure.
        """
        if wait_until not in VALID_WAIT_STATES:
            logger.warning(
                f"[Playwright] Unknown wait_until={wait_until!r}, "
                f"using 'domcontentloaded'"
            )
            wait_until = "domcontentloaded"

        page = None
        try:
            page = await self._ctx.new_page()
            response = await page.goto(url, wait_until=wait_until, timeout=timeout)
            if response is None:
                logger.warning(f"[Playwright] No response for {url}")
                return None

            if response.status >= 400:
                logger.warning(f"[Playwright] HTTP {response.status} for {url}")
                # Still return content — some sites return useful HTML even with 4xx
                html = await page.content()
                return html if html and len(html) > 500 else None

            html = await page.content()
            return html

        except Exception as e:
            logger.warning(f"[Playwright] Render failed {url}: {e}")
            return None

        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
