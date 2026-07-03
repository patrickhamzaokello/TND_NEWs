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


class MonitorNewsDjangoScraper:
    DEFAULT_SOURCE_NAME = "Daily Monitor"
    DEFAULT_BASE_URL = "https://www.monitor.co.ug"
    DEFAULT_NEWS_URL = "https://www.monitor.co.ug/uganda/news"
    MIN_FULL_CONTENT_WORDS = 80

    ARTICLE_URL_RE = re.compile(r"/uganda/(?:news|business|oped|sports|magazines|special-reports)/.+-\d{6,}/?$")
    BOILERPLATE_PATTERNS = (
        "subscribe for a month",
        "log in",
        "my account",
        "logging you out",
        "your subscription",
        "renew subscription",
        "maybe later",
        "what you need to know",
        "all rights reserved",
        "empower uganda",
        "daily monitor",
        "nation.africa",
        "email protected",
        "[email",
    )

    def __init__(self, source_name=DEFAULT_SOURCE_NAME):
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
        self.driver = None
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

    def setup_selenium_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        chrome_options.binary_location = "/usr/bin/chromium"
        service = Service(executable_path="/usr/bin/chromedriver")
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.driver.set_page_load_timeout(45)

    def cleanup_selenium_driver(self):
        if self.driver:
            try:
                self.driver.quit()
            finally:
                self.driver = None

    def log_message(self, run, level, message, article_url=""):
        ScrapingLog.objects.create(run=run, level=level, message=message, article_url=article_url)

    @staticmethod
    def _clean(text):
        return re.sub(r"\s+", " ", (text or "")).strip()

    def normalize_url(self, url):
        return urljoin(self.base_url + "/", (url or "").strip())

    def _fetch_soup(self, url, run=None, use_browser_fallback=True):
        try:
            response = self.session.get(url, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            if soup.find("body"):
                return soup
        except requests.RequestException as exc:
            if run:
                self.log_message(run, "warning", f"Requests fetch failed: {exc}", url)

        if not use_browser_fallback:
            return None

        try:
            if not self.driver:
                self.setup_selenium_driver()
            self.driver.get(url)
            WebDriverWait(self.driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(1.5)
            return BeautifulSoup(self.driver.page_source, "html.parser")
        except (TimeoutException, WebDriverException) as exc:
            if run:
                self.log_message(run, "error", f"Selenium fetch failed: {exc}", url)
            return None

    def _json_ld_nodes(self, soup):
        nodes = []
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

    def _article_json_ld(self, soup):
        article_types = {"NewsArticle", "Article", "ReportageNewsArticle"}
        for node in self._json_ld_nodes(soup):
            node_type = node.get("@type")
            types = set(node_type if isinstance(node_type, list) else [node_type])
            if types & article_types:
                return node
        return {}

    def _parse_date(self, value):
        value = self._clean(value)
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
        if lower in {"yesterday", "yesterday listen"}:
            return now - timedelta(days=1)
        try:
            parsed = date_parser.parse(value, fuzzy=True)
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
        except (ValueError, TypeError, OverflowError):
            return None

    def _external_id_from_url(self, url):
        match = re.search(r"-(\d{6,})/?$", url or "")
        if match:
            return match.group(1)
        return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]

    def _is_article_url(self, url):
        parsed = urlparse(url)
        if parsed.netloc and "monitor.co.ug" not in parsed.netloc:
            return False
        return bool(self.ARTICLE_URL_RE.search(parsed.path))

    def _category_from_url(self, url):
        parts = [part for part in urlparse(url).path.strip("/").split("/") if part]
        if len(parts) >= 3:
            return parts[2].replace("-", " ").title()
        if len(parts) >= 2:
            return parts[1].replace("-", " ").title()
        return "News"

    def _image_from_node(self, node):
        if not isinstance(node, dict):
            return ""
        image = node.get("image")
        if isinstance(image, str):
            return self.normalize_url(image)
        if isinstance(image, list) and image:
            first = image[0]
            return self.normalize_url(first.get("url", "") if isinstance(first, dict) else str(first))
        if isinstance(image, dict):
            return self.normalize_url(image.get("url", ""))
        return ""

    def _author_from_node(self, node):
        author = node.get("author") if isinstance(node, dict) else None
        if isinstance(author, list) and author:
            author = author[0]
        if isinstance(author, dict):
            return self._clean(author.get("name", "")), self.normalize_url(author.get("url", ""))
        if isinstance(author, str):
            return self._clean(author), ""
        return "", ""

    def _is_boilerplate(self, text):
        lower = text.lower()
        if len(text) < 25:
            return True
        return any(pattern in lower for pattern in self.BOILERPLATE_PATTERNS)

    def _paragraphs_from_container(self, soup):
        selectors = [
            "article p",
            "main article p",
            "[data-test-id*='article'] p",
            ".article-content p",
            ".article-body p",
            ".story-content p",
            ".entry-content p",
            ".paragraph-wrapper p",
            ".body-copy p",
            "main p",
        ]
        best = []
        for selector in selectors:
            paragraphs = []
            for element in soup.select(selector):
                if element.find_parent(["nav", "footer", "aside", "header"]):
                    continue
                text = self._clean(element.get_text(" ", strip=True))
                if text and not self._is_boilerplate(text):
                    paragraphs.append(text)
            if len(" ".join(paragraphs).split()) > len(" ".join(best).split()):
                best = paragraphs
        return list(dict.fromkeys(best))

    def _excerpt_from_paragraphs(self, paragraphs, max_length=260):
        if not paragraphs:
            return ""
        excerpt = paragraphs[0]
        return excerpt if len(excerpt) <= max_length else excerpt[:max_length].rsplit(" ", 1)[0] + "..."

    def get_or_create_category(self, category_name):
        name = (category_name or "News").strip()
        category, _ = Category.objects.get_or_create(slug=slugify(name), defaults={"name": name})
        return category

    def get_or_create_tag(self, tag_name):
        name = (tag_name or "").strip()
        if not name:
            return None
        tag, _ = Tag.objects.get_or_create(slug=slugify(name), defaults={"name": name})
        return tag

    def get_or_create_author(self, author_name, profile_url=""):
        name = self._clean(re.sub(r"^By\s+", "", author_name or "", flags=re.IGNORECASE))
        if not name:
            return None
        author, _ = Author.objects.get_or_create(
            name=name,
            source=self.source,
            defaults={"profile_url": profile_url or ""},
        )
        return author

    def _extract_listing_articles(self, soup):
        articles = []
        seen = set()
        for node in self._json_ld_nodes(soup):
            item_list = node.get("itemListElement") if isinstance(node, dict) else None
            if isinstance(item_list, list):
                for item in item_list:
                    target = item.get("item", item) if isinstance(item, dict) else {}
                    url = self.normalize_url(target.get("url", "")) if isinstance(target, dict) else ""
                    title = self._clean(target.get("name", "") if isinstance(target, dict) else "")
                    if url and self._is_article_url(url) and url not in seen:
                        seen.add(url)
                        articles.append({"url": url, "title": title, "featured_image": ""})

        for anchor in soup.select("a[href]"):
            url = self.normalize_url(anchor.get("href"))
            if not self._is_article_url(url) or url in seen:
                continue
            text = self._clean(anchor.get_text(" ", strip=True))
            img = anchor.find("img")
            title = text or self._clean(img.get("alt", "") if img else "")
            if not title or len(title) < 8:
                continue
            title = re.sub(r"^\s*(PRIME\s+)?", "", title, flags=re.IGNORECASE)
            title = re.sub(r"\s+(National|Education|Insight|World|Business|Sports|News)\s+.*$", "", title).strip()
            featured_image = self.normalize_url(img.get("src") or img.get("data-src") or "") if img else ""
            seen.add(url)
            articles.append(
                {
                    "url": url,
                    "title": title,
                    "featured_image": featured_image,
                    "category": self._category_from_url(url),
                }
            )
        return articles

    def scrape_full_article_content(self, article_url, run):
        soup = self._fetch_soup(article_url, run)
        if not soup:
            return None

        node = self._article_json_ld(soup)
        title = self._clean(node.get("headline", "")) if node else ""
        if not title:
            title_el = soup.select_one("h1")
            title = self._clean(title_el.get_text(" ", strip=True) if title_el else "")

        body = self._clean(node.get("articleBody", "")) if node else ""
        paragraphs = [self._clean(p) for p in re.split(r"\n{2,}|(?<=[.!?])\s+(?=[A-Z])", body) if self._clean(p)]
        if len(" ".join(paragraphs).split()) < self.MIN_FULL_CONTENT_WORDS:
            paragraphs = self._paragraphs_from_container(soup)

        author_name, author_url = self._author_from_node(node)
        if not author_name:
            author_el = soup.select_one("[rel='author'], .author, [class*='author']")
            author_name = self._clean(author_el.get_text(" ", strip=True) if author_el else "")

        published_at = None
        for candidate in [
            node.get("datePublished", "") if node else "",
            soup.select_one("meta[property='article:published_time']"),
            soup.select_one("time[datetime]"),
        ]:
            if hasattr(candidate, "get"):
                candidate = candidate.get("content") or candidate.get("datetime") or candidate.get_text(" ", strip=True)
            published_at = self._parse_date(candidate)
            if published_at:
                break

        image_url = self._image_from_node(node)
        if not image_url:
            image_meta = soup.select_one("meta[property='og:image'], meta[name='twitter:image']")
            image_url = self.normalize_url(image_meta.get("content", "")) if image_meta else ""

        caption_el = soup.select_one("figcaption, .image-caption, .caption")
        caption = self._clean(caption_el.get_text(" ", strip=True) if caption_el else "")

        tags = []
        for tag in soup.select("a[rel='tag'], .tags a, .article-tags a"):
            tag_text = self._clean(tag.get_text(" ", strip=True))
            if tag_text:
                tags.append(tag_text)

        full_content = "\n\n".join(paragraphs)
        word_count = len(full_content.split())
        return {
            "full_title": title,
            "full_content": full_content,
            "excerpt": self._excerpt_from_paragraphs(paragraphs),
            "word_count": word_count,
            "paragraph_count": len(paragraphs),
            "tags": list(dict.fromkeys(tags)),
            "image_caption": caption,
            "featured_image_url": image_url,
            "author": author_name,
            "author_url": author_url,
            "published_at": published_at,
            "has_full_content": word_count >= self.MIN_FULL_CONTENT_WORDS,
        }

    def _find_existing_article(self, article_data):
        url = article_data["url"]
        external_id = article_data.get("external_id") or self._external_id_from_url(url)
        return Article.find_existing(
            url,
            external_id,
            self.source,
            content_hash=article_data.get("content_hash", ""),
            title=article_data.get("title", ""),
        )

    def _apply_detail(self, article, detail):
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
        article.image_caption = detail.get("image_caption", "")
        article.has_full_content = bool(detail.get("has_full_content"))
        article.scrape_status = "complete" if article.has_full_content else "partial"
        article.last_scrape_error = "" if article.has_full_content else "Content below quality threshold or gated"
        if detail.get("featured_image_url"):
            article.featured_image_url = detail["featured_image_url"]
        if detail.get("published_at"):
            article.published_at = detail["published_at"]
        if detail.get("author"):
            article.author = self.get_or_create_author(detail["author"], detail.get("author_url", ""))

    def scrape_and_save(self, get_full_content=True, max_articles=None):
        run = ScrapingRun.objects.create(source=self.source, status="started")
        try:
            self.log_message(run, "info", f"Started scraping from {self.source.news_url}")
            soup = self._fetch_soup(self.source.news_url, run)
            if not soup:
                raise RuntimeError("Unable to fetch Daily Monitor listing page")

            article_items = self._extract_listing_articles(soup)
            run.articles_found = len(article_items)
            run.save(update_fields=["articles_found"])
            self.log_message(run, "info", f"Found {len(article_items)} candidate articles")

            if max_articles:
                article_items = article_items[:max_articles]

            for i, article_data in enumerate(article_items, start=1):
                article_url = article_data.get("url", "")
                if not article_url:
                    run.articles_skipped += 1
                    continue

                try:
                    article_data["external_id"] = self._external_id_from_url(article_url)
                    detail = self.scrape_full_article_content(article_url, run) if get_full_content else None
                    if detail:
                        article_data["content_hash"] = Article._hash_text(detail.get("full_content") or detail.get("excerpt"))

                    existing_article = self._find_existing_article(article_data)
                    if existing_article:
                        if get_full_content and (not existing_article.has_full_content or detail and detail.get("has_full_content")):
                            self._apply_detail(existing_article, detail)
                            existing_article.save()
                            if detail:
                                existing_article.tags.clear()
                                for tag_name in detail.get("tags", []):
                                    tag = self.get_or_create_tag(tag_name)
                                    if tag:
                                        existing_article.tags.add(tag)
                            run.articles_updated += 1
                            self.log_message(run, "info", f"Updated article: {existing_article.title}", article_url)
                        else:
                            run.articles_skipped += 1
                        continue

                    category = self.get_or_create_category(article_data.get("category") or self._category_from_url(article_url))
                    article = Article(
                        external_id=article_data["external_id"],
                        url=article_url,
                        title=article_data.get("title") or "Untitled Daily Monitor article",
                        excerpt="",
                        featured_image_url=article_data.get("featured_image", ""),
                        source=self.source,
                        category=category,
                    )
                    if get_full_content:
                        self._apply_detail(article, detail)
                    else:
                        article.scrape_status = "pending"

                    try:
                        article.save()
                    except IntegrityError:
                        run.articles_skipped += 1
                        continue

                    if detail:
                        for tag_name in detail.get("tags", []):
                            tag = self.get_or_create_tag(tag_name)
                            if tag:
                                article.tags.add(tag)

                    run.articles_added += 1
                    self.log_message(run, "info", f"Added new article: {article.title}", article_url)
                    time.sleep(1)

                except Exception as exc:
                    run.error_count += 1
                    self.log_message(run, "error", f"Error processing article {i}: {exc}", article_url)

            run.status = "completed"
            run.completed_at = timezone.now()
            run.save()
            self.source.last_successful_scrape_at = run.completed_at
            self.source.failure_count = 0
            self.source.save(update_fields=["last_successful_scrape_at", "failure_count"])
            self.log_message(
                run,
                "info",
                f"Scraping completed. Added: {run.articles_added}, Updated: {run.articles_updated}, "
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
            self.log_message(run, "error", f"Scraping failed: {exc}")
            raise
        finally:
            self.cleanup_selenium_driver()
