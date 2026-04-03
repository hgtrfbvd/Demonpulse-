from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 60000
DEFAULT_WAIT_MS = 2500
DEFAULT_VIEWPORT = {"width": 1440, "height": 2200}

BLOCK_PATTERNS = [
    "403 forbidden",
    "access denied",
    "request blocked",
    "captcha",
    "attention required",
    "cf-error",
    "cloudflare",
    "temporarily unavailable",
]

DEFAULT_WAIT_SELECTORS = [
    "body",
    "a[href]",
    "table",
    "tr",
]


@dataclass
class BrowserFetchResult:
    ok: bool
    url: str
    final_url: str | None = None
    status_code: int | None = None
    html: str = ""
    text: str = ""
    blocked: bool = False
    reason: str | None = None
    method: str = "playwright"
    duration_ms: int | None = None
    screenshot_path: str | None = None
    html_dump_path: str | None = None
    text_dump_path: str | None = None
    selector_hits: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BrowserClient:
    """
    Shared browser fetch utility for protected / JS-heavy racing sites.

    Purpose:
    - use browser only to get the rendered data
    - capture debug artifacts when needed
    - detect blocked pages early
    - give connectors one clean fetch interface

    Notes:
    - this is not app logic
    - this is not scraping logic
    - connectors still do the extraction
    """

    def __init__(
        self,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        wait_ms: int = DEFAULT_WAIT_MS,
        headless: bool = True,
        artifacts_dir: str = "data/browser_debug",
    ):
        self.timeout_ms = timeout_ms
        self.wait_ms = wait_ms
        self.headless = headless
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def fetch_page(
        self,
        url: str,
        *,
        wait_ms: int | None = None,
        extra_headers: dict[str, str] | None = None,
        wait_selectors: list[str] | None = None,
        save_debug: bool = False,
        debug_prefix: str | None = None,
        auto_scroll: bool = True,
    ) -> BrowserFetchResult:
        wait_ms = wait_ms if wait_ms is not None else self.wait_ms
        wait_selectors = wait_selectors or list(DEFAULT_WAIT_SELECTORS)
        started = time.time()

        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            return BrowserFetchResult(
                ok=False,
                url=url,
                blocked=False,
                reason=f"playwright_import_failed: {e}",
                duration_ms=int((time.time() - started) * 1000),
            )

        html = ""
        text = ""
        final_url = None
        status_code = None
        selector_hits: dict[str, int] = {}
        screenshot_path = None
        html_dump_path = None
        text_dump_path = None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ],
                )

                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    viewport=DEFAULT_VIEWPORT,
                    locale="en-AU",
                )

                page = context.new_page()
                headers = {
                    "Accept-Language": "en-AU,en;q=0.9",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                }
                if extra_headers:
                    headers.update(extra_headers)
                page.set_extra_http_headers(headers)

                response = page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                final_url = page.url
                status_code = response.status if response else None

                page.wait_for_timeout(wait_ms)

                matched_any = False
                for selector in wait_selectors:
                    try:
                        page.wait_for_selector(selector, timeout=4000)
                        matched_any = True
                    except Exception:
                        pass

                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                if auto_scroll:
                    self._auto_scroll(page)

                for selector in wait_selectors:
                    try:
                        selector_hits[selector] = page.locator(selector).count()
                    except Exception:
                        selector_hits[selector] = 0

                html = page.content() or ""
                text = self._safe_visible_text(page)

                blocked, reason = self._detect_blocked(html, text, status_code)

                if save_debug:
                    screenshot_path, html_dump_path, text_dump_path = self._save_debug_artifacts(
                        page=page,
                        html=html,
                        text=text,
                        prefix=debug_prefix or self._slug(url),
                    )

                browser.close()

                return BrowserFetchResult(
                    ok=bool(html) and not blocked and (matched_any or bool(text.strip())),
                    url=url,
                    final_url=final_url,
                    status_code=status_code,
                    html=html,
                    text=text,
                    blocked=blocked,
                    reason=reason,
                    method="playwright",
                    duration_ms=int((time.time() - started) * 1000),
                    screenshot_path=screenshot_path,
                    html_dump_path=html_dump_path,
                    text_dump_path=text_dump_path,
                    selector_hits=selector_hits,
                )

        except Exception as e:
            log.error("Browser fetch failed for %s: %s", url, e)
            return BrowserFetchResult(
                ok=False,
                url=url,
                final_url=final_url,
                status_code=status_code,
                html=html,
                text=text,
                blocked=False,
                reason=f"browser_fetch_failed: {e}",
                method="playwright",
                duration_ms=int((time.time() - started) * 1000),
                screenshot_path=screenshot_path,
                html_dump_path=html_dump_path,
                text_dump_path=text_dump_path,
                selector_hits=selector_hits or None,
            )

    def fetch_pdf_url(
        self,
        url: str,
        *,
        wait_ms: int | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> BrowserFetchResult:
        """
        Use browser to resolve a page that may redirect to or reveal a PDF-like endpoint.
        We still only use the browser to get the data path.
        """
        result = self.fetch_page(
            url,
            wait_ms=wait_ms,
            extra_headers=extra_headers,
            save_debug=False,
            auto_scroll=False,
        )

        if result.final_url and ".pdf" in result.final_url.lower():
            result.ok = True
            result.reason = "pdf_resolved"
            return result

        pdf_match = re.search(r'https?://[^"\'>\s]+\.pdf', result.html or "", flags=re.IGNORECASE)
        if pdf_match:
            result.final_url = pdf_match.group(0)
            result.ok = True
            result.reason = "pdf_found_in_dom"

        return result

    def _safe_visible_text(self, page) -> str:
        try:
            text = page.locator("body").inner_text(timeout=3000) or ""
            return " ".join(text.split())
        except Exception:
            return ""

    def _auto_scroll(self, page):
        try:
            page.evaluate(
                """
                async () => {
                    await new Promise((resolve) => {
                        let totalHeight = 0;
                        const distance = 700;
                        const timer = setInterval(() => {
                            const scrollHeight = document.body.scrollHeight;
                            window.scrollBy(0, distance);
                            totalHeight += distance;
                            if (totalHeight >= scrollHeight) {
                                clearInterval(timer);
                                resolve();
                            }
                        }, 250);
                    });
                }
                """
            )
            page.wait_for_timeout(1200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
        except Exception:
            pass

    def _detect_blocked(self, html: str, text: str, status_code: int | None) -> tuple[bool, str | None]:
        lower_html = (html or "").lower()
        lower_text = (text or "").lower()

        if status_code and status_code >= 400:
            return True, f"http_{status_code}"

        for pattern in BLOCK_PATTERNS:
            if pattern in lower_html or pattern in lower_text:
                return True, pattern

        if not html.strip() and not text.strip():
            return True, "empty_render"

        return False, None

    def _save_debug_artifacts(
        self,
        *,
        page,
        html: str,
        text: str,
        prefix: str,
    ) -> tuple[str | None, str | None, str | None]:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = f"{stamp}_{prefix}"

        screenshot_path = self.artifacts_dir / f"{base}.png"
        html_path = self.artifacts_dir / f"{base}.html"
        text_path = self.artifacts_dir / f"{base}.txt"

        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            screenshot_path = None

        try:
            html_path.write_text(html or "", encoding="utf-8")
        except Exception:
            html_path = None

        try:
            text_path.write_text(text or "", encoding="utf-8")
        except Exception:
            text_path = None

        return (
            str(screenshot_path) if screenshot_path else None,
            str(html_path) if html_path else None,
            str(text_path) if text_path else None,
        )

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
        return slug[:80] or "page"
