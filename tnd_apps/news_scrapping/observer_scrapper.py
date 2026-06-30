import hashlib
import json
import re
import time
from datetime import timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from django.db import IntegrityError
from django.utils import timezone
from django.utils.text import slugify
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .models import Article, Author, Category, NewsSource, ScrapingLog, ScrapingRun, Tag


def _build_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    options.binary_location = "/usr/bin/chromium"
    service = Service(executable_path="/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(45)
    return driver


class ObserverUgScraper:
    """
    Scraper for https://observer.ug — Uganda Observer, a leading Ugandan news outlet.

    Article URL pattern (WordPress):
        https://observer.ug/{category}/{slug}/
        e.g. /news/uganda-parliament-passes-budget/
             /business/nssf-reform-update/
    """

    DEFAULT_SOURCE_NAME = "The Observer"
    DEFAULT_BASE_URL = "https://observer.ug"
    DEFAULT_NEWS_URL = "https://observer.ug/news"
    MIN_FULL_CONTENT_WORDS = 80
    # Observer rate-limits aggressively; give it breathing room
    REQUEST_DELAY = 2.0
    PAGE_DELAY = 3.0

    # Two-segment path: /{known-section}/{slug-of-at-least-8-chars}/
    KNOWN_SECTIONS = (
        "news", "business", "education", "sports", "viewpoint",
        "lifestyle-entertainment", "technology", "topics",
    )
    ARTICLE_URL_RE = re.compile(
        r"^/(?:news|business|education|sports|viewpoint|lifestyle-entertainment|technology|topics)"
        r"/[a-z0-9][a-z0-9-]{7,}/?$",
        re.IGNORECASE,
    )

    BOILERPLATE_PATTERNS = (
        "all rights reserved",
        "observer.ug",
        "follow us on",
        "subscribe",
        "advertisement",
        "also read",
        "related stories",
        "you may also like",
        "privacy policy",
        "terms of use",
        "leave a reply",
        "your email address will not be published",
        "comment policy",
        "share this",
        "click here",
        "powered by",
        "this article",
        "email protected",
        "[email",
    )

    SECTIONS: dict[str, str] = {
        "news":                    "https://observer.ug/news",
        "business":                "https://observer.ug/business",
        "education":               "https://observer.ug/education",
        "sports":                  "https://observer.ug/sports",
        "viewpoint":               "https://observer.ug/viewpoint",
        "lifestyle-entertainment": "https://observer.ug/lifestyle-entertainment",
        "technology":              "https://observer.ug/technology",
    }

    def __init__(self, source_name: str = DEFAULT_SOURCE_NAME, headless: bool = True):
        self.source, _ = NewsSource.objects.get_or_create(
            name=source_name,
            defaults={
                "base_url": self.DEFAULT_BASE_URL,
                "news_url": self.DEFAULT_NEWS_URL,
                "reliability_tier": "high",
                "country": "Uganda",
                "language": "English",
            },
        )
        self.base_url = self.source.base_url.rstrip("/") or self.DEFAULT_BASE_URL
        self.headless = headless
        self.driver: webdriver.Chrome | None = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-UG,en;q=0.9",
                "Referer": "https://observer.ug/",
            }
        )

    # ── Driver ─────────────────────────────────────────────────────────────

    def _start_driver(self) -> None:
        if self.driver is None:
            self.driver = _build_driver(headless=self.headless)

    def _quit_driver(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def __del__(self):
        self._quit_driver()

    # ── Utilities ──────────────────────────────────────────────────────────

    def _log(self, run: ScrapingRun, level: str, message: str, url: str = "") -> None:
        ScrapingLog.objects.create(run=run, level=level, message=message, article_url=url)

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()

    def _absolute_url(self, url: str) -> str:
        if not url:
            return ""
        url = url.strip()
        if url.startswith("//"):
            return "https:" + url
        return urljoin(self.base_url + "/", url)

    def _is_article_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.netloc and "observer.ug" not in parsed.netloc:
            return False
        return bool(self.ARTICLE_URL_RE.match(parsed.path))

    def _is_boilerplate(self, text: str) -> bool:
        if len(text) < 20:
            return True
        lower = text.lower()
        return any(p in lower for p in self.BOILERPLATE_PATTERNS)

    def _category_from_url(self, url: str) -> str:
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        if parts:
            return parts[0].replace("-", " ").title()
        return "News"

    def _external_id_from_url(self, url: str) -> str:
        parts = [p for p in urlparse(url or "").path.strip("/").split("/") if p]
        # Use the slug (last segment) as the stable ID
        if len(parts) >= 2:
            return parts[-1][:64]
        return hashlib.sha1((url or "").encode()).hexdigest()[:16]

    # ── HTTP / browser fetch ───────────────────────────────────────────────

    def _fetch_soup(self, url: str, run: ScrapingRun | None = None) -> BeautifulSoup | None:
        # Observer rate-limits requests heavily; always try Selenium first for
        # listing pages, fall back to requests for article detail pages.
        try:
            resp = self.session.get(url, timeout=25)
            if resp.status_code == 429:
                raise requests.RequestException("429 rate limited")
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            if soup.find("body") and len(soup.find("body").get_text(strip=True)) > 100:
                return soup
        except requests.RequestException as exc:
            if run:
                self._log(run, "warning", f"Requests fetch failed: {exc}", url)

        try:
            self._start_driver()
            self.driver.get(url)
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(2.0)
            return BeautifulSoup(self.driver.page_source, "html.parser")
        except (TimeoutException, WebDriverException) as exc:
            if run:
                self._log(run, "error", f"Selenium fetch failed: {exc}", url)
            return None

    # ── JSON-LD helpers ────────────────────────────────────────────────────

    def _json_ld_nodes(self, soup: BeautifulSoup) -> list[dict]:
        nodes: list[dict] = []
        for script in soup.select("script[type='application/ld+json']"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            queue = parsed if isinstance(parsed, list) else [parsed]
            while queue:
                item = queue.pop(0)
                if isinstance(item, list):
                    queue.extend(item)
                elif isinstance(item, dict):
                    nodes.append(item)
                    graph = item.get("@graph")
                    if isinstance(graph, list):
                        queue.extend(graph)
        return nodes

    def _article_json_ld(self, soup: BeautifulSoup) -> dict:
        article_types = {"NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting"}
        for node in self._json_ld_nodes(soup):
            node_type = node.get("@type")
            types = set(node_type if isinstance(node_type, list) else [node_type])
            if types & article_types:
                return node
        return {}

    def _image_from_node(self, node: dict) -> str:
        image = node.get("image") if isinstance(node, dict) else None
        if isinstance(image, str):
            return self._absolute_url(image)
        if isinstance(image, list) and image:
            first = image[0]
            return self._absolute_url(first.get("url", "") if isinstance(first, dict) else str(first))
        if isinstance(image, dict):
            return self._absolute_url(image.get("url", ""))
        return ""

    def _author_from_node(self, node: dict) -> tuple[str, str]:
        author = node.get("author") if isinstance(node, dict) else None
        if isinstance(author, list) and author:
            author = author[0]
        if isinstance(author, dict):
            return self._clean(author.get("name", "")), self._absolute_url(author.get("url", ""))
        if isinstance(author, str):
            return self._clean(author), ""
        return "", ""

    # ── Date parsing ───────────────────────────────────────────────────────

    def _parse_date(self, value: str | None):
        value = self._clean(value or "")
        if not value:
            return None
        lower = value.lower()
        now = timezone.now()
        relative = re.search(r"(\d+)\s*(min|minute|hour|day)s?\s+ago", lower)
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)
            if unit.startswith("min"):
                return now - timedelta(minutes=amount)
            if unit.startswith("hour"):
                return now - timedelta(hours=amount)
            if unit.startswith("day"):
                return now - timedelta(days=amount)
        try:
            parsed = date_parser.parse(value, fuzzy=True)
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
        except (ValueError, TypeError, OverflowError):
            return None

    # ── ORM helpers ────────────────────────────────────────────────────────

    def _get_or_create_category(self, name: str) -> Category:
        name = (name or "News").strip() or "News"
        category, _ = Category.objects.get_or_create(
            slug=slugify(name)[:50], defaults={"name": name}
        )
        return category

    def _get_or_create_tag(self, name: str) -> Tag | None:
        name = (name or "").strip()
        if not name:
            return None
        tag, _ = Tag.objects.get_or_create(slug=slugify(name)[:50], defaults={"name": name})
        return tag

    def _get_or_create_author(self, name: str, profile_url: str = "") -> Author | None:
        name = self._clean(re.sub(r"^By\s+", "", name or "", flags=re.IGNORECASE))
        if not name:
            return None
        author, _ = Author.objects.get_or_create(
            name=name,
            source=self.source,
            defaults={"profile_url": profile_url or ""},
        )
        return author

    def _find_existing_article(self, url: str, external_id: str, content_hash: str = "") -> Article | None:
        canonical = Article.normalize_url(url)
        existing = (
            Article.objects.filter(external_id=external_id, source=self.source).first()
            or Article.objects.filter(canonical_url=canonical).first()
            or Article.objects.filter(url=url).first()
        )
        if not existing and content_hash:
            existing = Article.objects.filter(content_hash=content_hash).first()
        return existing

    # ── Listing page ───────────────────────────────────────────────────────

    def _scrape_listing_page(self, page_url: str, run: ScrapingRun) -> list[dict]:
        soup = self._fetch_soup(page_url, run)
        if not soup:
            self._log(run, "error", f"Failed to load listing page: {page_url}")
            return []

        results: list[dict] = []
        seen: set[str] = set()

        # 1. JSON-LD ItemList (Observer sometimes includes these)
        for node in self._json_ld_nodes(soup):
            item_list = node.get("itemListElement") if isinstance(node, dict) else None
            if not isinstance(item_list, list):
                continue
            for item in item_list:
                target = item.get("item", item) if isinstance(item, dict) else {}
                if not isinstance(target, dict):
                    continue
                url = self._absolute_url(target.get("url", ""))
                title = self._clean(target.get("name", "") or target.get("headline", ""))
                if url and self._is_article_url(url) and url not in seen:
                    seen.add(url)
                    results.append({"url": url, "title": title})

        # 2. WordPress article cards — Observer uses standard WP theme classes
        card_selectors = [
            "article.post",
            "article",
            ".td-module-container",
            ".td_module_flex",
            ".post",
            ".item-details",
            ".blog-item",
        ]
        for selector in card_selectors:
            for card in soup.select(selector):
                parsed = self._parse_card(card)
                if parsed and parsed["url"] not in seen:
                    seen.add(parsed["url"])
                    results.append(parsed)
            if len(results) >= 5:
                break

        # 3. Fallback — any anchor pointing to an article URL
        for anchor in soup.select("a[href]"):
            url = self._absolute_url(anchor.get("href", ""))
            if url in seen or not self._is_article_url(url):
                continue
            img = anchor.find("img")
            title = (
                self._clean(anchor.get_text(" ", strip=True))
                or self._clean(img.get("alt", "") if img else "")
            )
            if not title or len(title) < 8:
                continue
            featured_image = ""
            if img:
                featured_image = self._absolute_url(
                    img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
                )
            seen.add(url)
            results.append({
                "url": url,
                "title": title,
                "featured_image": featured_image,
                "category": self._category_from_url(url),
            })

        return results

    def _parse_card(self, card) -> dict | None:
        anchors = card.select("a[href]")
        article_anchor = None
        for anchor in anchors:
            url = self._absolute_url(anchor.get("href", ""))
            if self._is_article_url(url):
                article_anchor = anchor
                break
        if not article_anchor:
            return None

        url = self._absolute_url(article_anchor.get("href", ""))

        title_el = card.select_one(
            "h1 a, h2 a, h3 a, h4 a, .entry-title a, .td-module-title a, .post-title a"
        )
        img = card.select_one("img")
        title = self._clean(
            title_el.get_text(" ", strip=True) if title_el
            else article_anchor.get_text(" ", strip=True)
        )
        if not title and img:
            title = self._clean(img.get("alt", ""))
        if not title or len(title) < 8:
            return None

        featured_image = ""
        if img:
            featured_image = self._absolute_url(
                img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
            )

        author_el = card.select_one(
            ".td-post-author-name a, .author a, [rel='author'], [class*='author'] a"
        )
        author_name = self._clean(author_el.get_text(" ", strip=True) if author_el else "")
        author_url  = self._absolute_url(author_el.get("href", "") if author_el else "")

        date_el = card.select_one("time[datetime], [class*='date'], .td-post-date, .entry-date")
        published_value = ""
        if date_el:
            published_value = (
                date_el.get("datetime")
                or date_el.get("content")
                or date_el.get_text(" ", strip=True)
            )

        category_el = card.select_one(
            ".td-post-category a, .cat-links a, .cat-name a, [class*='category'] a, a[rel='category tag']"
        )
        category_name = (
            self._clean(category_el.get_text(" ", strip=True) if category_el else "")
            or self._category_from_url(url)
        )

        return {
            "url": url,
            "title": title,
            "featured_image": featured_image,
            "author_name": author_name,
            "author_url": author_url,
            "published_date_str": published_value,
            "category": category_name,
        }

    # ── Article detail page ────────────────────────────────────────────────

    def _paragraphs_from_soup(self, soup: BeautifulSoup) -> list[str]:
        selectors = [
            ".entry-content p",
            ".td-post-content p",
            ".post-content p",
            ".article-content p",
            "article p",
            "main article p",
            ".story-body p",
            "main p",
        ]
        best: list[str] = []
        for selector in selectors:
            paragraphs: list[str] = []
            for el in soup.select(selector):
                if el.find_parent(["nav", "footer", "aside", "header", "script", "style"]):
                    continue
                text = self._clean(el.get_text(" ", strip=True))
                if text and not self._is_boilerplate(text):
                    paragraphs.append(text)
            if len(" ".join(paragraphs).split()) > len(" ".join(best).split()):
                best = paragraphs
        return list(dict.fromkeys(best))

    def _scrape_article_detail(self, article_url: str, run: ScrapingRun) -> dict | None:
        soup = self._fetch_soup(article_url, run)
        if not soup:
            return None

        node = self._article_json_ld(soup)

        # Title — JSON-LD headline first, then h1
        title = self._clean(node.get("headline", "")) if node else ""
        if not title:
            title_el = soup.select_one("h1.entry-title, h1.td-post-title, h1.post-title, h1")
            title = self._clean(title_el.get_text(" ", strip=True) if title_el else "")

        # Body paragraphs
        body = self._clean(node.get("articleBody", "")) if node else ""
        paragraphs = [
            self._clean(p)
            for p in re.split(r"\n{2,}|(?<=[.!?])\s+(?=[A-Z])", body)
            if self._clean(p)
        ]
        if len(" ".join(paragraphs).split()) < self.MIN_FULL_CONTENT_WORDS:
            paragraphs = self._paragraphs_from_soup(soup)

        # Author
        author_name, author_url = self._author_from_node(node)
        if not author_name:
            author_el = soup.select_one(
                "[rel='author'], .author-name a, [class*='author'] a, .td-post-author-name a"
            )
            if author_el:
                author_name = self._clean(author_el.get_text(" ", strip=True))
                author_url  = self._absolute_url(author_el.get("href", ""))

        # Published date
        published_date = ""
        for candidate in [
            node.get("datePublished", "") if node else "",
            soup.select_one("meta[property='article:published_time']"),
            soup.select_one("time[datetime]"),
            soup.select_one(".entry-date, .td-post-date time, [class*='date']"),
        ]:
            if hasattr(candidate, "get"):
                candidate = (
                    candidate.get("content")
                    or candidate.get("datetime")
                    or candidate.get_text(" ", strip=True)
                )
            published_date = self._clean(candidate or "")
            if self._parse_date(published_date):
                break

        # Featured image
        featured_image = self._image_from_node(node)
        if not featured_image:
            for sel in [
                ".td-post-featured-image img",
                ".post-thumbnail img",
                "article figure img",
                ".wp-post-image",
                "meta[property='og:image']",
                "meta[name='twitter:image']",
            ]:
                el = soup.select_one(sel)
                if el:
                    featured_image = self._absolute_url(
                        el.get("content") or el.get("src") or el.get("data-src") or ""
                    )
                    if featured_image:
                        break

        # Excerpt — OG description preferred
        excerpt = ""
        for meta_sel in ["meta[property='og:description']", "meta[name='description']"]:
            meta = soup.select_one(meta_sel)
            if meta and meta.get("content"):
                excerpt = self._clean(meta.get("content"))
                break
        if not excerpt and paragraphs:
            first = paragraphs[0]
            excerpt = first if len(first) <= 260 else first[:260].rsplit(" ", 1)[0] + "..."

        # Caption
        caption_el = soup.select_one("figcaption, .wp-caption-text, .image-caption")
        image_alt = self._clean(caption_el.get_text(" ", strip=True) if caption_el else "")

        # Tags
        tags: list[str] = []
        for tag_el in soup.select("a[rel='tag'], .tags-links a, .post-tags a, .entry-tags a"):
            tag_text = self._clean(tag_el.get_text(" ", strip=True))
            if tag_text:
                tags.append(tag_text)

        # Category — from article:section meta, breadcrumb, or URL
        category_name = self._category_from_url(article_url)
        for sel in [
            "meta[property='article:section']",
            ".cat-links a",
            "a[rel='category tag']",
            ".breadcrumb a:last-of-type",
            ".entry-category a",
        ]:
            el = soup.select_one(sel)
            if el:
                text = self._clean(el.get("content") or el.get_text(" ", strip=True))
                if text and text.lower() not in ("home", "observer", "the observer"):
                    category_name = text
                    break

        full_content = "\n\n".join(paragraphs)
        word_count = len(full_content.split())
        return {
            "full_title": title,
            "full_content": full_content,
            "excerpt": excerpt,
            "word_count": word_count,
            "paragraph_count": len(paragraphs),
            "featured_image_url": featured_image,
            "image_alt": image_alt,
            "author_name": author_name,
            "author_url": author_url,
            "published_date_str": published_date,
            "published_at": self._parse_date(published_date),
            "tags": list(dict.fromkeys(tags)),
            "category": category_name,
            "has_full_content": word_count >= self.MIN_FULL_CONTENT_WORDS,
        }

    # ── Apply detail to Article ────────────────────────────────────────────

    def _apply_detail(self, article: Article, detail: dict | None) -> None:
        if not detail:
            article.scrape_status = "partial"
            article.last_scrape_error = "Article detail could not be fetched"
            return
        if detail.get("full_title"):
            article.title = detail["full_title"]
        article.content       = detail.get("full_content", "") or article.content
        article.excerpt       = detail.get("excerpt", "") or article.excerpt
        article.word_count    = detail.get("word_count", 0)
        article.paragraph_count = detail.get("paragraph_count", 0)
        article.image_caption = detail.get("image_alt", "")
        article.has_full_content = bool(detail.get("has_full_content"))
        article.scrape_status = "complete" if article.has_full_content else "partial"
        article.last_scrape_error = "" if article.has_full_content else "Content below quality threshold"
        if detail.get("featured_image_url"):
            article.featured_image_url = detail["featured_image_url"]
        if detail.get("published_at"):
            article.published_at = detail["published_at"]
        if detail.get("published_date_str"):
            article.published_time_str = detail["published_date_str"]
        if detail.get("author_name"):
            article.author = self._get_or_create_author(
                detail["author_name"], detail.get("author_url", "")
            )

    # ── Main entry point ───────────────────────────────────────────────────

    def scrape_and_save(
        self,
        get_full_content: bool = True,
        max_articles: int | None = None,
        start_page: int = 1,
        max_pages: int = 1,
        news_url: str | None = None,
    ) -> dict:
        listing_url = news_url or self.source.news_url or self.DEFAULT_NEWS_URL
        run = ScrapingRun.objects.create(source=self.source, status="started")

        try:
            self._log(run, "info", f"Observer UG scraper started. Base URL: {listing_url}")
            total_processed = 0

            for page_num in range(start_page, start_page + max_pages):
                if page_num == 1:
                    page_url = listing_url.rstrip("/") + "/"
                else:
                    page_url = listing_url.rstrip("/") + f"/page/{page_num}/"

                self._log(run, "info", f"Scraping listing page {page_num}: {page_url}")
                cards = self._scrape_listing_page(page_url, run)

                if not cards:
                    self._log(run, "warning", f"No articles found on page {page_num}. Stopping.")
                    break

                run.articles_found += len(cards)
                run.save(update_fields=["articles_found"])

                for idx, card in enumerate(cards, start=1):
                    if max_articles and total_processed >= max_articles:
                        break

                    article_url = card.get("url", "")
                    if not article_url:
                        run.articles_skipped += 1
                        continue

                    try:
                        external_id  = self._external_id_from_url(article_url)
                        detail       = self._scrape_article_detail(article_url, run) if get_full_content else None
                        content_hash = (
                            Article._hash_text(detail.get("full_content") or detail.get("excerpt"))
                            if detail else ""
                        )
                        existing = self._find_existing_article(article_url, external_id, content_hash)

                        if existing:
                            if get_full_content and (
                                not existing.has_full_content
                                or (detail and detail.get("has_full_content"))
                            ):
                                self._apply_detail(existing, detail)
                                existing.save()
                                if detail:
                                    existing.tags.clear()
                                    for tag_name in detail.get("tags", []):
                                        tag = self._get_or_create_tag(tag_name)
                                        if tag:
                                            existing.tags.add(tag)
                                run.articles_updated += 1
                                self._log(run, "info", f"Updated: {existing.title}", article_url)
                            else:
                                run.articles_skipped += 1
                            total_processed += 1
                            time.sleep(self.REQUEST_DELAY)
                            continue

                        cat_name = (
                            (detail.get("category") if detail else None)
                            or card.get("category")
                            or self._category_from_url(article_url)
                        )
                        category = self._get_or_create_category(cat_name)

                        article = Article(
                            external_id=external_id,
                            url=article_url,
                            title=card.get("title") or "Untitled Observer article",
                            featured_image_url=card.get("featured_image", ""),
                            source=self.source,
                            category=category,
                            published_time_str=card.get("published_date_str", ""),
                        )
                        if card.get("author_name"):
                            article.author = self._get_or_create_author(
                                card["author_name"], card.get("author_url", "")
                            )
                        if card.get("published_date_str"):
                            article.published_at = self._parse_date(card["published_date_str"])

                        if get_full_content:
                            self._apply_detail(article, detail)
                            if detail and detail.get("category"):
                                article.category = self._get_or_create_category(detail["category"])
                        else:
                            article.scrape_status = "pending"

                        try:
                            article.save()
                        except IntegrityError:
                            run.articles_skipped += 1
                            total_processed += 1
                            continue

                        if detail:
                            for tag_name in detail.get("tags", []):
                                tag = self._get_or_create_tag(tag_name)
                                if tag:
                                    article.tags.add(tag)

                        run.articles_added += 1
                        total_processed += 1
                        self._log(run, "info", f"Added: {article.title}", article_url)
                        time.sleep(self.REQUEST_DELAY)

                    except Exception as exc:
                        run.error_count += 1
                        self._log(
                            run, "error",
                            f"Error on article {idx} (page {page_num}): {exc}",
                            article_url,
                        )

                if max_articles and total_processed >= max_articles:
                    break
                if page_num < start_page + max_pages - 1:
                    time.sleep(self.PAGE_DELAY)

            run.status = "completed"
            run.completed_at = timezone.now()
            run.save()
            self.source.last_successful_scrape_at = run.completed_at
            self.source.failure_count = 0
            self.source.save(update_fields=["last_successful_scrape_at", "failure_count"])
            self._log(
                run, "info",
                f"Done. Added: {run.articles_added}, Updated: {run.articles_updated}, "
                f"Skipped: {run.articles_skipped}, Errors: {run.error_count}",
            )
            return {
                "run_id":             run.run_id,
                "articles_found":     run.articles_found,
                "articles_added":     run.articles_added,
                "articles_updated":   run.articles_updated,
                "articles_skipped":   run.articles_skipped,
                "errors":             run.error_count,
                "duration":           run.duration_seconds,
            }

        except Exception as exc:
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = timezone.now()
            run.save()
            self.source.failure_count += 1
            self.source.save(update_fields=["failure_count"])
            self._log(run, "error", f"Scraping failed: {exc}")
            raise
        finally:
            self._quit_driver()
