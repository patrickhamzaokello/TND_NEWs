import hashlib
import json
import re
import time
from datetime import datetime, timedelta
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


def build_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    options.binary_location = "/usr/bin/chromium"
    service = Service(executable_path="/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(45)
    return driver


class NilePostScraper:
    DEFAULT_SOURCE_NAME = "NilePost"
    DEFAULT_BASE_URL = "https://nilepost.co.ug"
    DEFAULT_NEWS_URL = "https://nilepost.co.ug/news"
    MIN_FULL_CONTENT_WORDS = 80
    REQUEST_DELAY = 1.0
    PAGE_DELAY = 1.5

    ARTICLE_URL_RE = re.compile(
        r"/(?:news|opinions|politics|security|business|education|health|crime|sports|climate-change|investigations|exclusive)/\d+/",
        re.IGNORECASE,
    )
    BOILERPLATE_PATTERNS = (
        "get breaking news first",
        "stay in the know",
        "tap yes",
        "yes, keep me updated",
        "not now",
        "all rights reserved",
        "nile post, a product of next media",
        "privacy policy",
        "advertise with us",
        "terms of use",
        "hot right now",
        "trending",
    )

    def __init__(self, source_name: str = DEFAULT_SOURCE_NAME, headless: bool = True):
        self.source, _ = NewsSource.objects.get_or_create(
            name=source_name,
            defaults={
                "base_url": self.DEFAULT_BASE_URL,
                "news_url": self.DEFAULT_NEWS_URL,
                "reliability_tier": "medium",
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
            }
        )

    def _start_driver(self) -> None:
        if self.driver is None:
            self.driver = build_driver(headless=self.headless)

    def _quit_driver(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def __del__(self):
        self._quit_driver()

    def _log(self, run: ScrapingRun, level: str, message: str, url: str = "") -> None:
        ScrapingLog.objects.create(run=run, level=level, message=message, article_url=url)

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()

    def _absolute_url(self, url: str) -> str:
        return urljoin(self.base_url + "/", (url or "").strip())

    def _fetch_soup(self, url: str, run: ScrapingRun | None = None) -> BeautifulSoup | None:
        try:
            response = self.session.get(url, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            if soup.find("body"):
                return soup
        except requests.RequestException as exc:
            if run:
                self._log(run, "warning", f"Requests fetch failed: {exc}", url)

        try:
            self._start_driver()
            self.driver.get(url)
            WebDriverWait(self.driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(1.5)
            return BeautifulSoup(self.driver.page_source, "html.parser")
        except (TimeoutException, WebDriverException) as exc:
            if run:
                self._log(run, "error", f"Selenium fetch failed: {exc}", url)
            return None

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

    def _parse_date(self, value: str | None) -> datetime | None:
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

    def _parse_iso_date(self, iso_string: str) -> datetime | None:
        return self._parse_date(iso_string)

    def _generate_excerpt(self, paragraphs: list[str], max_length: int = 250) -> str:
        if not paragraphs:
            return ""
        excerpt = paragraphs[0]
        return excerpt if len(excerpt) <= max_length else excerpt[:max_length].rsplit(" ", 1)[0] + "..."

    def _get_or_create_category(self, name: str) -> Category:
        name = (name or "News").strip() or "News"
        category, _ = Category.objects.get_or_create(slug=slugify(name)[:50], defaults={"name": name})
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

    def _external_id_from_url(self, url: str) -> str:
        match = re.search(r"/(\d+)(?:/|$)", url or "")
        if match:
            return match.group(1)
        return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]

    def _category_from_url(self, url: str) -> str:
        try:
            segment = urlparse(url).path.strip("/").split("/")[0]
            return segment.replace("-", " ").title() if segment else "News"
        except Exception:
            return "News"

    def _is_article_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.netloc and "nilepost.co.ug" not in parsed.netloc:
            return False
        return bool(self.ARTICLE_URL_RE.search(parsed.path))

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

    def _is_boilerplate(self, text: str) -> bool:
        lower = text.lower()
        if len(text) < 25:
            return True
        return any(pattern in lower for pattern in self.BOILERPLATE_PATTERNS)

    def _extract_listing_from_json_ld(self, soup: BeautifulSoup) -> list[dict]:
        articles = []
        seen = set()
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
                    articles.append({"url": url, "title": title, "category": self._category_from_url(url)})
        return articles

    def _parse_card(self, card) -> dict | None:
        anchors = card.select("a[href]")
        article_anchor = None
        for anchor in anchors:
            url = self._absolute_url(anchor.get("href"))
            if self._is_article_url(url):
                article_anchor = anchor
                break
        if not article_anchor:
            return None

        url = self._absolute_url(article_anchor.get("href"))
        title_el = card.select_one("[itemprop='headline'] a, h1 a, h2 a, h3 a, .news-title a")
        img = card.select_one("img")
        title = self._clean(title_el.get_text(" ", strip=True) if title_el else article_anchor.get_text(" ", strip=True))
        if not title and img:
            title = self._clean(img.get("alt", ""))
        if not title or len(title) < 8:
            return None

        author_el = card.select_one("[itemprop='author'], .author a, small a[href*='author']")
        date_el = card.select_one("[itemprop='datePublished'], time")
        featured_image = ""
        if img:
            featured_image = self._absolute_url(img.get("src") or img.get("data-src") or img.get("data-lazy-src") or "")

        published_value = ""
        if date_el:
            published_value = date_el.get("content") or date_el.get("datetime") or date_el.get_text(" ", strip=True)

        return {
            "url": url,
            "title": title,
            "featured_image": featured_image,
            "author_name": self._clean(author_el.get_text(" ", strip=True) if author_el else ""),
            "author_url": self._absolute_url(author_el.get("href", "")) if author_el and author_el.get("href") else "",
            "published_date_content": published_value,
            "published_date_text": self._clean(date_el.get_text(" ", strip=True) if date_el else ""),
            "category": self._category_from_url(url),
        }

    def _scrape_listing_page(self, page_url: str, run: ScrapingRun) -> list[dict]:
        soup = self._fetch_soup(page_url, run)
        if not soup:
            self._log(run, "error", f"Failed to load listing page: {page_url}")
            return []

        results = self._extract_listing_from_json_ld(soup)
        seen = {item["url"] for item in results}

        card_selectors = [
            "[itemscope][itemtype*='NewsArticle']",
            ".masonry-grid .masonry-item",
            "article",
            ".news-card",
            ".card",
            ".post",
            ".list-group-item",
        ]
        for selector in card_selectors:
            for card in soup.select(selector):
                parsed = self._parse_card(card)
                if parsed and parsed["url"] not in seen:
                    seen.add(parsed["url"])
                    results.append(parsed)

        for anchor in soup.select("a[href]"):
            url = self._absolute_url(anchor.get("href"))
            if url in seen or not self._is_article_url(url):
                continue
            img = anchor.find("img")
            title = self._clean(anchor.get_text(" ", strip=True)) or self._clean(img.get("alt", "") if img else "")
            if not title or len(title) < 8:
                continue
            seen.add(url)
            results.append(
                {
                    "url": url,
                    "title": title,
                    "featured_image": self._absolute_url(img.get("src") or img.get("data-src") or "") if img else "",
                    "category": self._category_from_url(url),
                }
            )

        return results

    def _paragraphs_from_soup(self, soup: BeautifulSoup) -> list[str]:
        selectors = [
            ".blog-details-wrapper .special-section.content-body p",
            ".nile_post_apr05-main-left p",
            "article .content-body p",
            "article p",
            "main article p",
            ".entry-content p",
            ".post-content p",
            ".article-content p",
            "main p",
        ]
        best = []
        for selector in selectors:
            paragraphs = []
            for paragraph in soup.select(selector):
                if paragraph.find_parent(["nav", "footer", "aside", "header", "script", "style"]):
                    continue
                text = self._clean(paragraph.get_text(" ", strip=True))
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
        title = self._clean(node.get("headline", "")) if node else ""
        if not title:
            title_el = soup.select_one("h1.nile_post_apr05-title, h1")
            title = self._clean(title_el.get_text(" ", strip=True) if title_el else "")

        body = self._clean(node.get("articleBody", "")) if node else ""
        paragraphs = [self._clean(p) for p in re.split(r"\n{2,}|(?<=[.!?])\s+(?=[A-Z])", body) if self._clean(p)]
        if len(" ".join(paragraphs).split()) < self.MIN_FULL_CONTENT_WORDS:
            paragraphs = self._paragraphs_from_soup(soup)

        author_name, author_url = self._author_from_node(node)
        if not author_name:
            meta = soup.select_one(".nile_post_apr05-meta, [class*='meta']")
            author_link = meta.select_one("a") if meta else None
            author_name = self._clean(author_link.get_text(" ", strip=True) if author_link else "")
            author_url = self._absolute_url(author_link.get("href", "")) if author_link and author_link.get("href") else ""

        published_date = ""
        for candidate in [
            node.get("datePublished", "") if node else "",
            soup.select_one("meta[property='article:published_time']"),
            soup.select_one("time[datetime]"),
            soup.select_one(".nile_post_apr05-meta"),
        ]:
            if hasattr(candidate, "get"):
                candidate = candidate.get("content") or candidate.get("datetime") or candidate.get_text(" ", strip=True)
            published_date = self._clean(candidate or "")
            if self._parse_date(published_date):
                break

        featured_image = self._image_from_node(node)
        if not featured_image:
            img = soup.select_one(".nile_post_apr05-hero img, article img, meta[property='og:image'], meta[name='twitter:image']")
            featured_image = self._absolute_url(img.get("content") or img.get("src") or img.get("data-src") or "") if img else ""

        excerpt_el = soup.select_one(".nile_post_apr05-excerpt, meta[name='description'], meta[property='og:description']")
        excerpt = ""
        if excerpt_el:
            excerpt = self._clean(excerpt_el.get("content") if excerpt_el.name == "meta" else excerpt_el.get_text(" ", strip=True))
        if not excerpt:
            excerpt = self._generate_excerpt(paragraphs)

        caption_el = soup.select_one("figcaption, .image-caption, .wp-caption-text, .caption")
        tags = []
        for tag in soup.select(".nile_post_apr05-tags a, a[rel='tag'], .tags a, .article-tags a"):
            tag_text = self._clean(tag.get_text(" ", strip=True))
            if tag_text:
                tags.append(tag_text)

        full_content = "\n\n".join(paragraphs)
        word_count = len(full_content.split())
        return {
            "full_title": title,
            "full_content": full_content,
            "excerpt": excerpt,
            "word_count": word_count,
            "paragraph_count": len(paragraphs),
            "featured_image_url": featured_image,
            "image_alt": self._clean(caption_el.get_text(" ", strip=True) if caption_el else ""),
            "author_name": author_name,
            "author_url": author_url,
            "published_date_str": published_date,
            "published_at": self._parse_date(published_date),
            "tags": list(dict.fromkeys(tags)),
            "has_full_content": word_count >= self.MIN_FULL_CONTENT_WORDS,
        }

    def _find_existing_article(self, article_url: str, external_id: str, content_hash: str = "") -> Article | None:
        canonical_url = Article.normalize_url(article_url)
        existing = (
            Article.objects.filter(external_id=external_id, source=self.source).first()
            or Article.objects.filter(canonical_url=canonical_url).first()
            or Article.objects.filter(url=article_url).first()
        )
        if not existing and content_hash:
            existing = Article.objects.filter(content_hash=content_hash).first()
        return existing

    def _apply_detail(self, article: Article, detail: dict | None) -> None:
        if not detail:
            article.scrape_status = "partial"
            article.last_scrape_error = "Article detail could not be fetched"
            return
        if detail.get("full_title"):
            article.title = detail["full_title"]
        article.content = detail.get("full_content", "") or article.content
        article.excerpt = detail.get("excerpt", "") or article.excerpt
        article.word_count = detail.get("word_count", 0)
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
            article.author = self._get_or_create_author(detail["author_name"], detail.get("author_url", ""))

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
            self._log(run, "info", f"NilePost scraper started. Base URL: {listing_url}")
            total_processed = 0

            for page_num in range(start_page, start_page + max_pages):
                page_url = listing_url.rstrip("/") + "/" if page_num == 1 else listing_url.rstrip("/") + f"/page/{page_num}/"
                self._log(run, "info", f"Scraping listing page {page_num}: {page_url}")
                article_cards = self._scrape_listing_page(page_url, run)

                if not article_cards:
                    self._log(run, "warning", f"No articles found on page {page_num}. Stopping.")
                    break

                run.articles_found += len(article_cards)
                run.save(update_fields=["articles_found"])

                for idx, card in enumerate(article_cards, start=1):
                    if max_articles and total_processed >= max_articles:
                        break

                    article_url = card.get("url", "")
                    if not article_url:
                        run.articles_skipped += 1
                        continue

                    try:
                        external_id = self._external_id_from_url(article_url)
                        detail = self._scrape_article_detail(article_url, run) if get_full_content else None
                        content_hash = Article._hash_text(detail.get("full_content") or detail.get("excerpt")) if detail else ""
                        existing = self._find_existing_article(article_url, external_id, content_hash)

                        if existing:
                            if get_full_content and (not existing.has_full_content or detail and detail.get("has_full_content")):
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

                        category = self._get_or_create_category(card.get("category") or self._category_from_url(article_url))
                        article = Article(
                            external_id=external_id,
                            url=article_url,
                            title=card.get("title") or "Untitled NilePost article",
                            featured_image_url=card.get("featured_image", ""),
                            source=self.source,
                            category=category,
                            published_time_str=card.get("published_date_text", "") or card.get("published_date_content", ""),
                        )
                        if card.get("author_name"):
                            article.author = self._get_or_create_author(card["author_name"], card.get("author_url", ""))
                        if card.get("published_date_content"):
                            article.published_at = self._parse_date(card["published_date_content"])

                        if get_full_content:
                            self._apply_detail(article, detail)
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
                        self._log(run, "error", f"Error on article {idx} (page {page_num}): {exc}", article_url)

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
                run,
                "info",
                f"Done. Added: {run.articles_added}, Updated: {run.articles_updated}, "
                f"Skipped: {run.articles_skipped}, Errors: {run.error_count}",
            )

            return {
                "run_id": run.run_id,
                "articles_found": run.articles_found,
                "articles_added": run.articles_added,
                "articles_updated": run.articles_updated,
                "articles_skipped": run.articles_skipped,
                "errors": run.error_count,
                "duration": run.duration_seconds,
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
