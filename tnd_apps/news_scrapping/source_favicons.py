from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


FAVICON_REL_PRIORITY = (
    "icon",
    "shortcut icon",
    "apple-touch-icon",
    "apple-touch-icon-precomposed",
    "mask-icon",
)


class SourceFaviconResolver:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
        })

    def resolve(self, source) -> str:
        base_url = (source.base_url or source.news_url or "").strip()
        if not base_url:
            return ""
        root_url = self._site_root(base_url)

        html = self._fetch_html(base_url) or self._fetch_html(root_url)
        candidates = self._extract_candidates(html, root_url) if html else []
        candidates.append(urljoin(root_url, "/favicon.ico"))

        seen = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if self._looks_like_image(candidate):
                return candidate
        return ""

    def _site_root(self, url: str) -> str:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = "https://" + url
            parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/"

    def _fetch_html(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException:
            return ""
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type and response.text.lstrip()[:1] != "<":
            return ""
        return response.text

    def _extract_candidates(self, html: str, root_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links = []

        for rel_name in FAVICON_REL_PRIORITY:
            for link in soup.find_all("link"):
                rel_values = " ".join(link.get("rel") or []).lower()
                if rel_name not in rel_values:
                    continue
                href = link.get("href", "").strip()
                if href and not href.startswith("data:"):
                    links.append(urljoin(root_url, href))

        return links

    def _looks_like_image(self, url: str) -> bool:
        try:
            response = self.session.head(url, timeout=self.timeout, allow_redirects=True)
            if response.status_code >= 400 or not response.headers.get("content-type"):
                response = self.session.get(url, timeout=self.timeout, stream=True)
            response.raise_for_status()
        except requests.RequestException:
            return False

        content_type = response.headers.get("content-type", "").lower()
        return (
            content_type.startswith("image/")
            or urlparse(response.url).path.lower().endswith((".ico", ".png", ".jpg", ".jpeg", ".svg", ".webp"))
        )
