import hashlib
import json
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from django.db import IntegrityError
from django.utils import timezone
from django.utils.text import slugify

from .models import Article, Author, Category, NewsSource, ScrapingLog, ScrapingRun, Tag


class UBCScraper:
    DEFAULT_SOURCE_NAME = "UBC"
    DEFAULT_BASE_URL = "https://ubc.go.ug"
    DEFAULT_NEWS_URL = "https://ubc.go.ug/category/news/"
    MIN_FULL_CONTENT_WORDS = 70
    REQUEST_DELAY = 0.8
    PAGE_DELAY = 1.2

    EXCLUDED_PATH_PREFIXES = (
        "category/",
        "tag/",
        "author/",
        "wp-content/",
        "wp-json/",
        "feed/",
        "search/",
        "about",
        "contact",
        "privacy",
    )
    BOILERPLATE_PATTERNS = (
        "facebook",
        "twitter",
        "pinterest",
        "whatsapp",
        "previous article",
        "next article",
        "related articles",
        "more from author",
        "uganda broadcasting corporation",
        "share",
    )

    def __init__(self, source_name: str = DEFAULT_SOURCE_NAME):
        self.source, _ = NewsSource.objects.get_or_create(
            name=source_name,
            defaults={
                "base_url": self.DEFAULT_BASE_URL,
                "news_url": self.DEFAULT_NEWS_URL,
                "reliability_tier": "medium",
                "ownership": "Uganda Broadcasting Corporation",
                "country": "Uganda",
                "language": "English",
            },
        )
        source_updates = []
        if not self.source.base_url:
            self.source.base_url = self.DEFAULT_BASE_URL
            source_updates.append("base_url")
        if not self.source.news_url or self.source.news_url.rstrip("/") == self.DEFAULT_BASE_URL:
            self.source.news_url = self.DEFAULT_NEWS_URL
            source_updates.append("news_url")
        if source_updates:
            self.source.save(update_fields=source_updates)

        self.base_url = (self.source.base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-UG,en;q=0.9",
                "Cache-Control": "no-cache",
            }
        )
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=3)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _log(self, run: ScrapingRun, level: str, message: str, url: str = "") -> None:
        ScrapingLog.objects.create(run=run, level=level, message=message, article_url=url)

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()

    def _absolute_url(self, url: str) -> str:
        url = (url or "").strip()
        if not url:
            return ""
        return urljoin(self.base_url + "/", url)

    def _fetch_soup(self, url: str, run: ScrapingRun | None = None) -> BeautifulSoup | None:
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except requests.RequestException as exc:
            if run:
                self._log(run, "error", f"Failed to fetch page: {exc}", url)
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
        article_types = {"NewsArticle", "Article", "BlogPosting", "ReportageNewsArticle"}
        fallback = {}
        for node in self._json_ld_nodes(soup):
            node_type = node.get("@type")
            types = set(node_type if isinstance(node_type, list) else [node_type])
            if types & article_types:
                if node.get("articleBody"):
                    return node
                if not fallback:
                    fallback = node
        return fallback

    def _parse_date(self, value: str | None) -> datetime | None:
        value = self._clean(value or "")
        if not value:
            return None
        try:
            parsed = date_parser.parse(value, fuzzy=True)
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
        except (ValueError, TypeError, OverflowError):
            return None

    def _external_id_from_url(self, url: str) -> str:
        match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/([^/]+)/?", url or "")
        if match:
            return hashlib.sha1("|".join(match.groups()).encode("utf-8")).hexdigest()[:16]
        return hashlib.sha1(Article.normalize_url(url).encode("utf-8")).hexdigest()[:16]

    def _is_article_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.netloc and "ubc.go.ug" not in parsed.netloc:
            return False
        path = parsed.path.strip("/")
        if not path:
            return False
        if path.startswith(self.EXCLUDED_PATH_PREFIXES):
            return False
        return bool(re.match(r"^\d{4}/\d{2}/\d{2}/[^/]+/?$", path))

    def _image_url(self, element) -> str:
        if not element:
            return ""
        src = (
            element.get("data-img-url")
            or element.get("data-lazy-src")
            or element.get("data-src")
            or element.get("src")
            or element.get("content")
            or ""
        )
        if src.startswith("data:"):
            src = element.get("data-img-url") or element.get("data-lazy-src") or element.get("data-src") or ""
        if not src:
            srcset = element.get("data-lazy-srcset") or element.get("data-srcset") or element.get("srcset") or ""
            if srcset:
                src = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
        if not src:
            style = element.get("style", "")
            match = re.search(r"url\(['\"]?([^'\")]+)", style)
            if match:
                src = match.group(1)
        return self._absolute_url(src)

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

    def _get_or_create_category(self, name: str) -> Category:
        name = self._clean(name or "News") or "News"
        category, _ = Category.objects.get_or_create(slug=slugify(name), defaults={"name": name})
        return category

    def _get_or_create_tag(self, name: str) -> Tag | None:
        name = self._clean(name or "")
        if not name:
            return None
        tag, _ = Tag.objects.get_or_create(slug=slugify(name), defaults={"name": name})
        return tag

    def _get_or_create_author(self, name: str, profile_url: str = "") -> Author | None:
        name = self._clean(re.sub(r"^By\s+", "", name or "", flags=re.IGNORECASE))
        if not name:
            name = "Uganda Broadcasting Corporation"
        author, _ = Author.objects.get_or_create(
            name=name,
            source=self.source,
            defaults={"profile_url": profile_url or ""},
        )
        return author

    def _is_boilerplate(self, text: str) -> bool:
        if len(text) < 20:
            return True
        lower = text.lower()
        return any(pattern in lower for pattern in self.BOILERPLATE_PATTERNS)

    def _generate_excerpt(self, paragraphs: list[str], max_length: int = 240) -> str:
        if not paragraphs:
            return ""
        excerpt = paragraphs[0]
        return excerpt if len(excerpt) <= max_length else excerpt[:max_length].rsplit(" ", 1)[0] + "..."

    def _parse_listing_card(self, card) -> dict | None:
        title_link = card.select_one(
            ".td-module-title a[href], h3.entry-title a[href], .entry-title a[href], "
            "h1 a[href], h2 a[href], h3 a[href]"
        )
        thumb_link = card.select_one(".td-module-thumb a[href], a.td-image-wrap[href], a[rel='bookmark'][href]")
        anchor = title_link or thumb_link
        if not anchor:
            return None

        url = self._absolute_url(anchor.get("href", ""))
        if not self._is_article_url(url):
            return None

        title = self._clean(anchor.get("title", "") or anchor.get_text(" ", strip=True))
        if not title or len(title) < 8:
            return None

        image_el = card.select_one("[data-img-url], img.entry-thumb, img")
        date_el = card.select_one("time[datetime], .td-post-date time, .td-post-date")
        category_el = card.select_one(".td-post-category")
        excerpt_el = card.select_one(".td-excerpt")

        published_date = ""
        if date_el:
            published_date = date_el.get("datetime") or date_el.get_text(" ", strip=True)

        return {
            "url": url,
            "title": title,
            "featured_image": self._image_url(image_el),
            "published_date_text": self._clean(published_date),
            "category": self._clean(category_el.get_text(" ", strip=True) if category_el else "News") or "News",
            "excerpt": self._clean(excerpt_el.get_text(" ", strip=True) if excerpt_el else ""),
        }

    def _parse_anchor_listing_item(self, anchor) -> dict | None:
        url = self._absolute_url(anchor.get("href", ""))
        if not self._is_article_url(url):
            return None

        card = anchor.find_parent(
            class_=re.compile(
                r"td_module|td-module|td-block-span|td-big-grid-post|td-trending-now-post",
                re.IGNORECASE,
            )
        )
        title = self._clean(anchor.get("title", "") or anchor.get_text(" ", strip=True))
        if (not title or len(title) < 8) and card:
            title_el = card.select_one(".td-module-title a, .entry-title a, h1 a, h2 a, h3 a")
            title = self._clean(title_el.get("title", "") or title_el.get_text(" ", strip=True)) if title_el else title
        if not title or len(title) < 8:
            return None

        image_el = None
        if card:
            image_el = card.select_one("[data-img-url], img.entry-thumb, img")
        if image_el is None:
            image_el = anchor.select_one("[data-img-url], img")

        date_el = card.select_one("time[datetime], .td-post-date time, .td-post-date") if card else None
        category_el = card.select_one(".td-post-category") if card else None
        excerpt_el = card.select_one(".td-excerpt") if card else None
        published_date = ""
        if date_el:
            published_date = date_el.get("datetime") or date_el.get_text(" ", strip=True)

        return {
            "url": url,
            "title": title,
            "featured_image": self._image_url(image_el),
            "published_date_text": self._clean(published_date),
            "category": self._clean(category_el.get_text(" ", strip=True) if category_el else "News") or "News",
            "excerpt": self._clean(excerpt_el.get_text(" ", strip=True) if excerpt_el else ""),
        }

    def _scrape_listing_page(self, page_url: str, run: ScrapingRun) -> list[dict]:
        soup = self._fetch_soup(page_url, run)
        if not soup:
            return []

        articles = []
        seen = set()
        selectors = (
            ".td_module_wrap",
            ".td_module_flex",
            "article",
            ".td-block-inner .td_module_1",
            ".td_block_inner .td_module_wrap",
        )
        for selector in selectors:
            for card in soup.select(selector):
                parsed = self._parse_listing_card(card)
                if parsed and parsed["url"] not in seen:
                    seen.add(parsed["url"])
                    articles.append(parsed)

        for anchor in soup.select("a[href]"):
            parsed = self._parse_anchor_listing_item(anchor)
            if parsed and parsed["url"] not in seen:
                seen.add(parsed["url"])
                articles.append(parsed)
        return articles

    def _paragraphs_from_body(self, body: str) -> list[str]:
        if not body:
            return []
        body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
        raw_parts = [part for part in re.split(r"\n\s*\n+", body) if part.strip()]
        if len(raw_parts) <= 1:
            raw_parts = [part for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", body) if part.strip()]
        paragraphs = []
        for part in raw_parts:
            text = self._clean(part)
            if text and not self._is_boilerplate(text):
                paragraphs.append(text)
        return paragraphs

    def _paragraphs_from_soup(self, soup: BeautifulSoup) -> list[str]:
        selectors = [
            ".td-post-content.tagdiv-type p",
            ".td-post-content p",
            "article .td-post-content p",
            "article[itemtype*='Article'] p",
        ]
        best: list[str] = []
        for selector in selectors:
            paragraphs = []
            for paragraph in soup.select(selector):
                if paragraph.find_parent(["aside", "footer", "header", "nav", "script", "style"]):
                    continue
                if paragraph.find_parent(class_=re.compile(r"td-post-sharing|td-related|author-box|td_block_related", re.IGNORECASE)):
                    continue
                text = self._clean(paragraph.get_text(" ", strip=True))
                if text and not self._is_boilerplate(text):
                    paragraphs.append(text)
            if len(" ".join(paragraphs).split()) > len(" ".join(best).split()):
                best = paragraphs
        return list(dict.fromkeys(best))

    def _tags_from_node(self, node: dict) -> list[str]:
        keywords = node.get("keywords") if isinstance(node, dict) else None
        if isinstance(keywords, str):
            return [self._clean(tag) for tag in re.split(r",|;", keywords) if self._clean(tag)]
        if isinstance(keywords, list):
            return [self._clean(str(tag)) for tag in keywords if self._clean(str(tag))]
        return []

    def _scrape_article_detail(self, article_url: str, run: ScrapingRun) -> dict | None:
        soup = self._fetch_soup(article_url, run)
        if not soup:
            return None

        node = self._article_json_ld(soup)
        title = self._clean(node.get("headline", "")) if node else ""
        if not title:
            title_el = soup.select_one("h1.entry-title, .td-post-title h1, h1")
            title = self._clean(title_el.get_text(" ", strip=True) if title_el else "")

        body = node.get("articleBody", "") if node else ""
        paragraphs = self._paragraphs_from_body(body)
        if len(" ".join(paragraphs).split()) < self.MIN_FULL_CONTENT_WORDS:
            soup_paragraphs = self._paragraphs_from_soup(soup)
            if len(" ".join(soup_paragraphs).split()) > len(" ".join(paragraphs).split()):
                paragraphs = soup_paragraphs

        author_name, author_url = self._author_from_node(node)
        if not author_name:
            author_el = soup.select_one(".td-author-name a, a[rel='author'], .author a")
            author_name = self._clean(author_el.get_text(" ", strip=True) if author_el else "")
            author_url = self._absolute_url(author_el.get("href", "")) if author_el and author_el.get("href") else ""

        published_value = ""
        for candidate in (
            node.get("datePublished", "") if node else "",
            soup.select_one("meta[itemprop='datePublished']"),
            soup.select_one("meta[property='article:published_time']"),
            soup.select_one("time[datetime]"),
            soup.select_one(".td-post-date time"),
        ):
            if hasattr(candidate, "get"):
                candidate = candidate.get("content") or candidate.get("datetime") or candidate.get_text(" ", strip=True)
            published_value = self._clean(candidate or "")
            if self._parse_date(published_value):
                break

        category = ""
        category_el = soup.select_one(".td-category .entry-category a, .td-post-category")
        if category_el:
            category = self._clean(category_el.get_text(" ", strip=True))

        featured_image = self._image_from_node(node)
        if not featured_image:
            img = soup.select_one(
                "meta[property='og:image'], meta[name='twitter:image'], "
                ".td-post-featured-image img, article img.entry-thumb"
            )
            featured_image = self._image_url(img)

        excerpt = self._clean(node.get("description", "")) if node else ""
        if not excerpt:
            meta_description = soup.select_one("meta[name='description'], meta[property='og:description']")
            excerpt = self._clean(meta_description.get("content", "") if meta_description else "")
        if not excerpt:
            excerpt = self._generate_excerpt(paragraphs)

        tags = self._tags_from_node(node)
        for tag in soup.select(".td-tags a, a[rel='tag']"):
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
            "image_caption": "",
            "author_name": author_name,
            "author_url": author_url,
            "published_date_str": published_value,
            "published_at": self._parse_date(published_value),
            "category": category or "News",
            "tags": list(dict.fromkeys(tags)),
            "has_full_content": word_count >= self.MIN_FULL_CONTENT_WORDS,
        }

    def _find_existing_article(self, article_url: str, external_id: str, content_hash: str = "", title: str = "") -> Article | None:
        return Article.find_existing(article_url, external_id, self.source, content_hash, title)

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
        article.image_caption = detail.get("image_caption", "")
        article.has_full_content = bool(detail.get("has_full_content"))
        article.scrape_status = "complete" if article.has_full_content else "partial"
        article.last_scrape_error = "" if article.has_full_content else "Content below quality threshold"
        if detail.get("featured_image_url"):
            article.featured_image_url = detail["featured_image_url"]
        if detail.get("published_at"):
            article.published_at = detail["published_at"]
        if detail.get("published_date_str"):
            article.published_time_str = detail["published_date_str"]
        if detail.get("category"):
            article.category = self._get_or_create_category(detail["category"])
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
            self._log(run, "info", f"UBC scraper started. Base URL: {listing_url}")
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
                        if get_full_content and detail and not detail.get("has_full_content"):
                            self._log(
                                run,
                                "warning",
                                f"Detail content below threshold: {detail.get('word_count', 0)} words",
                                article_url,
                            )
                        elif get_full_content and not detail:
                            self._log(run, "warning", "No article detail content returned", article_url)

                        content_hash = Article._hash_text(detail.get("full_content") or detail.get("excerpt")) if detail else ""
                        existing = self._find_existing_article(article_url, external_id, content_hash, title=card.get("title", ""))

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

                        category = self._get_or_create_category(card.get("category", "News"))
                        article = Article(
                            external_id=external_id,
                            url=article_url,
                            title=card.get("title") or "Untitled UBC article",
                            excerpt=card.get("excerpt", ""),
                            featured_image_url=card.get("featured_image", ""),
                            source=self.source,
                            category=category,
                            published_time_str=card.get("published_date_text", ""),
                        )
                        if card.get("published_date_text"):
                            article.published_at = self._parse_date(card["published_date_text"])

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
