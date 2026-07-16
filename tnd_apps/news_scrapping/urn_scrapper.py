"""
Scraper for https://ugandaradionetwork.net — Uganda Radio Network (URN),
Uganda's largest independent news agency.

URN's archive page is a Handlebars app fed by a JSON endpoint, so this scraper
talks to the API directly instead of parsing HTML:

    GET /a/json/archive.php?page=N     (0-indexed, ~14 stories per page)

The JSON includes the FULL article body (tContents), summary, categories,
author, keywords, image, and publish time — richer than the public web page,
which paywalls the body.

Story page URL (for Article.url): https://ugandaradionetwork.net{permalink}
"""

import re
import time

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from django.db import IntegrityError
from django.utils import timezone
from django.utils.text import slugify

from .models import Article, Author, Category, NewsSource, ScrapingLog, ScrapingRun, Tag

BASE_URL = "https://ugandaradionetwork.net"
API_URL = "https://ugandaradionetwork.net/a/json/archive.php"
MIN_FULL_CONTENT_WORDS = 60
REQUEST_DELAY = 1.0


class UrnScraper:

    DEFAULT_SOURCE_NAME = "Uganda Radio Network"

    def __init__(self, source_name: str = DEFAULT_SOURCE_NAME, headless: bool = True):
        # `headless` accepted for interface parity with the Selenium scrapers; unused.
        self.source, _ = NewsSource.objects.get_or_create(
            name=source_name,
            defaults={
                "base_url": BASE_URL,
                "news_url": API_URL,
                "reliability_tier": "high",
                "country": "Uganda",
                "language": "English",
            },
        )
        self._driver = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://ugandaradionetwork.net/a/archive.php",
        })

    def _quit_driver(self) -> None:
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def __del__(self):
        self._quit_driver()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _log(self, run: ScrapingRun, level: str, message: str, url: str = "") -> None:
        ScrapingLog.objects.create(run=run, level=level, message=message, article_url=url)

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Convert URN's tContents HTML fragment to clean paragraph text."""
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        text = text.replace("\xa0", " ")
        # Collapse mid-sentence linebreaks (URN wraps hard); keep blank-line paragraphs
        paragraphs = [
            re.sub(r"\s+", " ", p).strip()
            for p in re.split(r"\n\s*\n", text)
        ]
        # If no blank-line splits, treat single newlines as soft wraps
        if len(paragraphs) <= 1:
            joined = re.sub(r"\s*\n\s*", " ", text)
            paragraphs = [re.sub(r"\s+", " ", joined).strip()]
        return "\n\n".join(p for p in paragraphs if p)

    def _parse_date(self, value: str | None):
        if not value:
            return None
        try:
            parsed = date_parser.parse(value)
            if timezone.is_naive(parsed):
                # URN timestamps are East Africa Time
                from zoneinfo import ZoneInfo
                parsed = parsed.replace(tzinfo=ZoneInfo("Africa/Kampala"))
            return parsed
        except (ValueError, TypeError, OverflowError):
            return None

    def _get_or_create_category(self, name: str) -> Category:
        name = (name or "News").strip() or "News"
        category, _ = Category.objects.get_or_create(
            slug=slugify(name)[:50], defaults={"name": name}
        )
        return category

    def _get_or_create_author(self, story: dict) -> Author | None:
        submitter = story.get("submitter") or {}
        name = " ".join(
            part for part in [submitter.get("tFirstName"), submitter.get("tLastName")] if part
        ).strip() or (story.get("tAuthor") or "").strip()
        if not name:
            return None
        author, _ = Author.objects.get_or_create(name=name, source=self.source)
        return author

    def _tags_from_keywords(self, story: dict) -> list[str]:
        tags: list[str] = []
        keywords = story.get("keywords") or {}
        groups = keywords.values() if isinstance(keywords, dict) else keywords
        for group in groups:
            if not isinstance(group, list):
                continue
            for kw in group:
                name = (kw.get("tName") or "").strip() if isinstance(kw, dict) else ""
                if name and name not in tags:
                    tags.append(name)
        return tags[:10]

    def _flaresolverr_fetch(self, url: str, run: ScrapingRun) -> dict:
        """
        URN sits behind a Cloudflare *managed* challenge that blocks datacenter
        IPs (plain requests AND vanilla headless Chromium both fail). Route the
        request through the FlareSolverr sidecar, which solves the challenge
        with a real browser and returns the response body.

        Requires the `flaresolverr` service in docker-compose:
            image: ghcr.io/flaresolverr/flaresolverr:latest
        Endpoint configurable via settings.FLARESOLVERR_URL.
        """
        import json as _json

        from django.conf import settings

        endpoint = getattr(settings, 'FLARESOLVERR_URL', 'http://flaresolverr:8191/v1')

        try:
            resp = requests.post(
                endpoint,
                json={"cmd": "request.get", "url": url, "maxTimeout": 60000},
                timeout=75,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") != "ok":
                self._log(run, "error", f"FlareSolverr error: {payload.get('message', 'unknown')}", url)
                return {}

            body = payload.get("solution", {}).get("response", "")
            # The browser wraps raw JSON in an HTML/pre shell — extract it
            start = body.find("{")
            end = body.rfind("}")
            if start == -1 or end == -1:
                self._log(run, "error", "FlareSolverr response contained no JSON", url)
                return {}
            return _json.loads(body[start:end + 1])

        except requests.RequestException as exc:
            self._log(
                run, "error",
                f"FlareSolverr unreachable ({exc}) — is the flaresolverr container running?",
                url,
            )
            return {}
        except ValueError as exc:
            self._log(run, "error", f"FlareSolverr JSON parse failed: {exc}", url)
            return {}

    def _fetch_page(self, page_index: int, run: ScrapingRun) -> dict:
        # Same filters the public archive page uses: statusId=4 = published
        params = {
            "statusId": 4,
            "authorOrUser": "author",
            "userType": 0,
            "voucherMatch": "=",
            "page": page_index,
        }
        try:
            resp = self.session.get(API_URL, params=params, timeout=25)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            self._log(run, "warning", f"Direct API fetch failed for page {page_index} ({exc}) — trying FlareSolverr")

        # Cloudflare managed challenge — go through the FlareSolverr sidecar
        from urllib.parse import urlencode
        return self._flaresolverr_fetch(f"{API_URL}?{urlencode(params)}", run)

    # ── Main entry point ───────────────────────────────────────────────────

    def scrape_and_save(
        self,
        get_full_content: bool = True,
        max_articles: int | None = None,
        start_page: int = 1,
        max_pages: int = 2,
        news_url: str | None = None,
    ) -> dict:
        run = ScrapingRun.objects.create(source=self.source, status="started")

        try:
            self._log(run, "info", f"URN API scraper started | pages {start_page}..{start_page + max_pages - 1}")
            total_processed = 0

            for page_num in range(start_page, start_page + max_pages):
                data = self._fetch_page(page_num - 1, run)  # API is 0-indexed
                stories = data.get("results") or {}
                if isinstance(stories, dict):
                    stories = list(stories.values())
                # Newest first
                stories.sort(key=lambda s: s.get("sLive") or "", reverse=True)

                if not stories:
                    self._log(run, "warning", f"No stories on API page {page_num}. Stopping.")
                    break

                run.articles_found += len(stories)
                run.save(update_fields=["articles_found"])

                for story in stories:
                    if max_articles and total_processed >= max_articles:
                        break

                    try:
                        story_id = str(story.get("iID") or "")
                        permalink = story.get("permalink") or ""
                        if not story_id or not permalink:
                            run.articles_skipped += 1
                            continue

                        article_url = BASE_URL + permalink
                        title = (story.get("tTitle") or "").strip()
                        content = self._html_to_text(story.get("tContents") or "")
                        excerpt = re.sub(r"\s+", " ", story.get("tSummary") or "").strip()
                        if not excerpt and content:
                            excerpt = content[:260].rsplit(" ", 1)[0] + "..."

                        content_hash = Article._hash_text(content or excerpt)
                        existing = Article.find_existing(
                            article_url, story_id, self.source, content_hash, title=title
                        )
                        if existing:
                            run.articles_skipped += 1
                            total_processed += 1
                            continue

                        categories = story.get("categories") or []
                        cat_name = (
                            categories[0].get("tName") if categories and isinstance(categories[0], dict) else "News"
                        )
                        word_count = len(content.split())
                        paragraphs = content.split("\n\n") if content else []

                        article = Article(
                            external_id=story_id,
                            url=article_url,
                            title=title or "Untitled URN story",
                            content=content,
                            excerpt=excerpt,
                            word_count=word_count,
                            paragraph_count=len(paragraphs),
                            featured_image_url=story.get("imageUrl") or "",
                            source=self.source,
                            category=self._get_or_create_category(cat_name),
                            author=self._get_or_create_author(story),
                            published_at=self._parse_date(story.get("sLive") or story.get("sTime")),
                            published_time_str=story.get("sLive") or "",
                            has_full_content=word_count >= MIN_FULL_CONTENT_WORDS,
                        )
                        article.scrape_status = "complete" if article.has_full_content else "partial"

                        try:
                            article.save()
                        except IntegrityError:
                            run.articles_skipped += 1
                            total_processed += 1
                            continue

                        for tag_name in self._tags_from_keywords(story):
                            tag, _ = Tag.objects.get_or_create(
                                slug=slugify(tag_name)[:50], defaults={"name": tag_name}
                            )
                            article.tags.add(tag)

                        run.articles_added += 1
                        total_processed += 1
                        self._log(run, "info", f"Added: {article.title}", article_url)

                    except Exception as exc:
                        run.error_count += 1
                        self._log(run, "error", f"Error on story {story.get('iID')}: {exc}")

                if max_articles and total_processed >= max_articles:
                    break
                time.sleep(REQUEST_DELAY)

            run.status = "completed"
            run.completed_at = timezone.now()
            run.save()
            self.source.last_successful_scrape_at = run.completed_at
            self.source.failure_count = 0
            self.source.save(update_fields=["last_successful_scrape_at", "failure_count"])
            self._log(
                run, "info",
                f"Done. Added: {run.articles_added}, Skipped: {run.articles_skipped}, Errors: {run.error_count}",
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
