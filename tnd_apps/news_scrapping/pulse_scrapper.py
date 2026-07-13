"""
Scraper for https://www.pulse.ug — Pulse Uganda (Ringier).

Article URL pattern:
    /story/{headline-slug}-{timestamp}
    e.g. /story/breaking-justice-baguma-orders-state-funded-lawyer-2026071314194467923

Sections: /news, /entertainment, /lifestyle, /business
(Sports lives on pulsesports.ug — separate domain, not scraped here.)

Reuses the Observer scraper machinery (JSON-LD extraction, Selenium fallback,
ORM helpers) — only URL recognition, sections, and category mapping differ.
"""

import re
from urllib.parse import urlparse

from .observer_scrapper import ObserverUgScraper


class PulseUgScraper(ObserverUgScraper):

    DEFAULT_SOURCE_NAME = "Pulse Uganda"
    DEFAULT_BASE_URL = "https://www.pulse.ug"
    DEFAULT_NEWS_URL = "https://www.pulse.ug/news"
    MIN_FULL_CONTENT_WORDS = 60  # Pulse articles run shorter than Observer's

    # /story/{slug}-{timestamp-digits}
    ARTICLE_URL_RE = re.compile(
        r"^/story/[a-z0-9][a-z0-9-]{10,}-\d{10,}/?$",
        re.IGNORECASE,
    )

    SECTIONS: dict[str, str] = {
        "news":          "https://www.pulse.ug/news",
        "entertainment": "https://www.pulse.ug/entertainment",
        "lifestyle":     "https://www.pulse.ug/lifestyle",
        "business":      "https://www.pulse.ug/business",
    }

    BOILERPLATE_PATTERNS = ObserverUgScraper.BOILERPLATE_PATTERNS + (
        "pulse.ug",
        "pulse uganda",
        "pulse sports",
        "join our whatsapp channel",
        "get the latest news",
        "download the app",
        "recommended articles",
        "next article",
        "eyewitness? submit",
    )

    def __init__(self, source_name: str = DEFAULT_SOURCE_NAME,
                 headless: bool = True, default_category: str = "News"):
        super().__init__(source_name=source_name, headless=headless)
        self.default_category = default_category
        self.session.headers.update({"Referer": "https://www.pulse.ug/"})

    def _is_article_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.netloc and "pulse.ug" not in parsed.netloc:
            return False
        # Exclude the sports subdomain — different site
        if parsed.netloc.startswith("pulsesports"):
            return False
        return bool(self.ARTICLE_URL_RE.match(parsed.path))

    def _category_from_url(self, url: str) -> str:
        # Pulse article URLs are /story/... with no section segment; use the
        # section this scrape run was launched for. Detail pages usually still
        # provide article:section meta which overrides this.
        return self.default_category

    def _external_id_from_url(self, url: str) -> str:
        # The trailing timestamp digits are the stable unique ID
        match = re.search(r"-(\d{10,})/?$", urlparse(url or "").path)
        if match:
            return match.group(1)[:64]
        return super()._external_id_from_url(url)
