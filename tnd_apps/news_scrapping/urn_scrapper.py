"""
Scraper for https://ugandaradionetwork.net — Uganda Radio Network (URN),
Uganda's largest independent news agency.

Listing:
    https://ugandaradionetwork.net/a/archive.php          (reverse-chronological)
    pagination via query string: archive.php?page=N       (0-indexed)

Article URL pattern:
    /story/{headline-slug}
    (also appears as /a/story/{slug} in some links)

NOTE: URN partially paywalls article bodies ("log in and be a client to read
this story in full"). The public page exposes the headline, lead paragraph(s),
category, keywords, author, and image — enough for enrichment and story
matching, so the full-content word threshold is lowered accordingly.

Reuses the Observer scraper machinery (JSON-LD extraction, Selenium fallback,
ORM helpers).
"""

import re
from urllib.parse import urlparse

from .observer_scrapper import ObserverUgScraper


class UrnScraper(ObserverUgScraper):

    DEFAULT_SOURCE_NAME = "Uganda Radio Network"
    DEFAULT_BASE_URL = "https://ugandaradionetwork.net"
    DEFAULT_NEWS_URL = "https://ugandaradionetwork.net/a/archive.php"
    # URN paywalls full bodies — the public lead paragraph(s) run 40-120 words
    MIN_FULL_CONTENT_WORDS = 40

    # /story/{slug} or /a/story/{slug}
    ARTICLE_URL_RE = re.compile(
        r"^/(?:a/)?story/[a-z0-9][a-z0-9-]{9,}-?/?$",
        re.IGNORECASE,
    )

    SECTIONS: dict[str, str] = {
        "archive": "https://ugandaradionetwork.net/a/archive.php",
    }

    BOILERPLATE_PATTERNS = ObserverUgScraper.BOILERPLATE_PATTERNS + (
        "ugandaradionetwork",
        "uganda radio network",
        "log in and be a client",
        "you need to log in",
        "read this story in full",
        "keywords",
        "top story",
    )

    def __init__(self, source_name: str = DEFAULT_SOURCE_NAME, headless: bool = True):
        super().__init__(source_name=source_name, headless=headless)
        self.session.headers.update({"Referer": "https://ugandaradionetwork.net/"})

    def _listing_page_url(self, listing_url: str, page_num: int) -> str:
        # archive.php?page=N, 0-indexed; page 1 = bare archive.php
        if page_num == 1:
            return listing_url
        return f"{listing_url}?page={page_num - 1}"

    def _is_article_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.netloc and "ugandaradionetwork" not in parsed.netloc:
            return False
        return bool(self.ARTICLE_URL_RE.match(parsed.path))

    def _category_from_url(self, url: str) -> str:
        # /story/... carries no section — detail page's category tag or
        # article:section meta overrides this default
        return "News"

    def _external_id_from_url(self, url: str) -> str:
        parts = [p for p in urlparse(url or "").path.strip("/").split("/") if p]
        if parts:
            return parts[-1][:64]  # the slug
        return super()._external_id_from_url(url)
