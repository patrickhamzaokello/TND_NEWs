import time
import re
from datetime import datetime
from django.utils import timezone
from django.utils.text import slugify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)

from .models import Article, Category, Tag, Author, NewsSource, ScrapingRun, ScrapingLog


def build_driver(headless: bool = True) -> webdriver.Chrome:
    """
    Build and return a configured Chrome WebDriver instance.

    Args:
        headless: Run Chrome in headless mode (no visible window). Default True.

    Returns:
        Configured Chrome WebDriver.
    """
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
    # Suppress excessive logging
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.add_argument("--log-level=3")

    options.binary_location = "/usr/bin/chromium"
    service = Service(executable_path="/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(45)
    return driver


class NilePostScraper:
    """
    Selenium-based scraper for NilePost (nilepost.co.ug).

    Article list structure
    ----------------------
    The listing page uses a masonry grid::

        <div class="masonry-grid">
            <div class="masonry-item" itemscope itemtype="https://schema.org/NewsArticle">
                <a href="..." itemprop="url"> ... </a>
                <div class="news-title mt-2" itemprop="headline">
                    <a href="...">Article Title</a>
                </div>
                <div class="list-group-heading">
                    <small><a href="..." itemprop="author">Author Name</a></small>
                    <span>|</span>
                    <small itemprop="datePublished" content="2026-02-22T15:28:40+03:00">
                        24 minutes ago
                    </small>
                </div>
            </div>
            ...
        </div>

    Article detail structure
    ------------------------
    Each article page uses the ``nile_post_apr05_body`` layout::

        <div class="nile_post_apr05_body">
            <main class="nile_post_apr05_wrapper">
                <header>
                    <h1 class="nile_post_apr05-title">Article Title</h1>
                    <div class="nile_post_apr05-meta">
                        By <a href="...">Author</a> | <a href="#">Date String</a>
                    </div>
                </header>
                <div class="nile_post_apr05-hero">
                    <img src="..." alt="...">
                </div>
                <div class="nile_post_apr05-main">
                    <article class="nile_post_apr05-main-left">
                        <div class="nile_post_apr05-excerpt">Excerpt text...</div>
                        <div class="blog-details-wrapper">
                            <div class="special-section content-body">
                                <p>Paragraph content...</p>
                                ...
                            </div>
                        </div>
                    </article>
                </div>
            </main>
        </div>
    """

    # ------------------------------------------------------------------ #
    #  Constants                                                           #
    # ------------------------------------------------------------------ #

    DEFAULT_SOURCE_NAME = "NilePost"
    DEFAULT_BASE_URL = "https://nilepost.co.ug"
    DEFAULT_NEWS_URL = "https://nilepost.co.ug/opinions"   # change as needed

    # Selenium wait timeout (seconds)
    WAIT_TIMEOUT = 20

    # Polite delay between requests (seconds)
    REQUEST_DELAY = 1.5
    PAGE_DELAY = 2.5

    # ------------------------------------------------------------------ #
    #  Initialisation                                                      #
    # ------------------------------------------------------------------ #

    def __init__(self, source_name: str = DEFAULT_SOURCE_NAME, headless: bool = True):
        try:
            self.source = NewsSource.objects.get(name=source_name)
        except NewsSource.DoesNotExist:
            self.source = NewsSource.objects.create(
                name=source_name,
                base_url=self.DEFAULT_BASE_URL,
                news_url=self.DEFAULT_NEWS_URL,
            )

        self.headless = headless
        self.driver: webdriver.Chrome | None = None

    # ------------------------------------------------------------------ #
    #  Driver lifecycle                                                    #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Logging helpers                                                     #
    # ------------------------------------------------------------------ #

    def _log(self, run: ScrapingRun, level: str, message: str, url: str = "") -> None:
        ScrapingLog.objects.create(run=run, level=level, message=message, article_url=url)

    # ------------------------------------------------------------------ #
    #  Django model helpers                                                #
    # ------------------------------------------------------------------ #

    def _get_or_create_category(self, name: str) -> Category:
        name = (name or "uncategorised").strip() or "uncategorised"
        slug = slugify(name)
        category, _ = Category.objects.get_or_create(slug=slug, defaults={"name": name})
        return category

    def _get_or_create_tag(self, name: str) -> Tag | None:
        name = (name or "").strip()
        if not name:
            return None
        slug = slugify(name)
        tag, _ = Tag.objects.get_or_create(slug=slug, defaults={"name": name})
        return tag

    def _get_or_create_author(self, name: str, profile_url: str = "") -> Author:
        name = (name or "unknown").strip() or "unknown"
        author, _ = Author.objects.get_or_create(
            name=name,
            source=self.source,
            defaults={"profile_url": profile_url},
        )
        return author

    # ------------------------------------------------------------------ #
    #  Utility                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clean(text: str) -> str:
        """Normalise whitespace in a string."""
        return re.sub(r"\s+", " ", (text or "")).strip()

    def _parse_iso_date(self, iso_string: str) -> datetime | None:
        """
        Parse ISO 8601 datetime string (e.g. '2026-02-22T15:28:40+03:00').
        Returns an aware datetime or None on failure.
        """
        if not iso_string:
            return None
        # Strip timezone offset for fromisoformat compatibility on older Python
        try:
            # Python 3.11+ handles +HH:MM natively; for older versions strip it
            return datetime.fromisoformat(iso_string)
        except ValueError:
            try:
                # Remove timezone part and treat as UTC
                clean = re.sub(r"[+-]\d{2}:\d{2}$", "", iso_string)
                dt = datetime.fromisoformat(clean)
                return timezone.make_aware(dt, timezone.utc)
            except ValueError:
                return None

    def _generate_excerpt(self, paragraphs: list[str], max_length: int = 250) -> str:
        """Build a short excerpt from a list of paragraph strings."""
        excerpt_parts: list[str] = []
        total = 0
        for para in paragraphs:
            if total >= max_length:
                break
            remaining = max_length - total
            if len(para) <= remaining:
                excerpt_parts.append(para)
                total += len(para)
            else:
                excerpt_parts.append(para[:remaining].rstrip() + "…")
                break
        return " ".join(excerpt_parts)

    # ------------------------------------------------------------------ #
    #  Page navigation helpers                                             #
    # ------------------------------------------------------------------ #

    def _get_page(self, url: str) -> bool:
        """
        Navigate to *url* and wait for the page body to load.

        Returns:
            True on success, False on timeout / error.
        """
        try:
            self.driver.get(url)
            WebDriverWait(self.driver, self.WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            return True
        except (TimeoutException, WebDriverException):
            return False

    # ------------------------------------------------------------------ #
    #  Article LIST page parsing                                           #
    # ------------------------------------------------------------------ #
    #
    # Target structure:
    #
    #   <div class="masonry-grid">
    #     <div class="masonry-item"
    #          itemscope itemtype="https://schema.org/NewsArticle">
    #
    #       <!-- URL (also used as image link) -->
    #       <a href="/opinions/123/slug" itemprop="url" ...>
    #         <div class="img-wrapper ...">
    #           <img src="https://..." itemprop="image" ...>
    #         </div>
    #       </a>
    #
    #       <!-- Headline -->
    #       <div class="news-title mt-2" itemprop="headline">
    #         <a href="/Opinions/123/slug">Article Title</a>
    #       </div>
    #
    #       <!-- Author + date -->
    #       <div class="list-group-heading">
    #         <small>
    #           <a href="/author/..." itemprop="author">Author Name</a>
    #         </small>
    #         <span>|</span>
    #         <small itemprop="datePublished"
    #                content="2026-02-22T15:28:40+03:00">
    #           24 minutes ago
    #         </small>
    #       </div>
    #
    #     </div>
    #   </div>
    # ------------------------------------------------------------------ #

    def _parse_article_card(self, card_el) -> dict | None:
        """
        Extract metadata from a single ``div.masonry-item`` element.

        Returns a dict with keys:
            url, title, featured_image, author_name, author_url,
            published_at, published_date_content
        or None if the card is missing a URL.
        """
        try:
            data: dict = {}

            # --- URL -------------------------------------------------------
            # The <a itemprop="url"> is the primary URL anchor
            url_el = card_el.find_element(By.CSS_SELECTOR, "a[itemprop='url']")
            data["url"] = url_el.get_attribute("href") or ""

            if not data["url"]:
                return None

            # --- Featured image --------------------------------------------
            try:
                img_el = card_el.find_element(By.CSS_SELECTOR, "a[itemprop='url'] img")
                data["featured_image"] = (
                    img_el.get_attribute("src")
                    or img_el.get_attribute("data-src")
                    or ""
                )
            except NoSuchElementException:
                data["featured_image"] = ""

            # --- Title (itemprop="headline") --------------------------------
            try:
                headline_el = card_el.find_element(
                    By.CSS_SELECTOR, "div[itemprop='headline'] a"
                )
                data["title"] = self._clean(headline_el.text)
                # Use canonical URL from headline link if different
                headline_url = headline_el.get_attribute("href") or ""
                if headline_url:
                    data["url"] = headline_url
            except NoSuchElementException:
                data["title"] = ""

            # --- Author (itemprop="author") ---------------------------------
            try:
                author_el = card_el.find_element(
                    By.CSS_SELECTOR, "small a[itemprop='author']"
                )
                data["author_name"] = self._clean(author_el.text)
                data["author_url"] = author_el.get_attribute("href") or ""
            except NoSuchElementException:
                data["author_name"] = ""
                data["author_url"] = ""

            # --- Published date (itemprop="datePublished") ------------------
            # The <small> carries a machine-readable `content` attribute
            try:
                date_el = card_el.find_element(
                    By.CSS_SELECTOR, "small[itemprop='datePublished']"
                )
                data["published_date_content"] = date_el.get_attribute("content") or ""
                data["published_date_text"] = self._clean(date_el.text)  # "24 minutes ago"
            except NoSuchElementException:
                data["published_date_content"] = ""
                data["published_date_text"] = ""

            return data

        except Exception:
            return None

    def _scrape_listing_page(self, page_url: str, run: ScrapingRun) -> list[dict]:
        """
        Load a listing page and return a list of article-card dicts.
        """
        if not self._get_page(page_url):
            self._log(run, "error", f"Failed to load listing page: {page_url}")
            return []

        # Wait for the masonry grid to be present
        try:
            WebDriverWait(self.driver, self.WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.masonry-grid"))
            )
        except TimeoutException:
            self._log(run, "warning", f"Masonry grid not found on: {page_url}")
            return []

        cards = self.driver.find_elements(
            By.CSS_SELECTOR, "div.masonry-grid div.masonry-item[itemscope]"
        )

        results: list[dict] = []
        for card in cards:
            parsed = self._parse_article_card(card)
            if parsed:
                results.append(parsed)

        return results

    # ------------------------------------------------------------------ #
    #  Article DETAIL page parsing                                         #
    # ------------------------------------------------------------------ #
    #
    # Target structure (nile_post_apr05 layout):
    #
    #   <div class="nile_post_apr05_body">
    #     <main class="nile_post_apr05_wrapper">
    #
    #       <header>
    #         <h1 class="nile_post_apr05-title">Title</h1>
    #         <div class="nile_post_apr05-meta">
    #           By <a href="...">Author Name</a>
    #           | <a href="#">Sunday, February 22, 2026</a>
    #         </div>
    #       </header>
    #
    #       <!-- Hero / featured image -->
    #       <div class="nile_post_apr05-hero">
    #         <img src="..." alt="...">
    #       </div>
    #
    #       <div class="nile_post_apr05-main">
    #         <article class="nile_post_apr05-main-left">
    #
    #           <!-- Lead excerpt shown in styled box -->
    #           <div class="nile_post_apr05-excerpt">Excerpt text</div>
    #
    #           <!-- Full body content -->
    #           <div class="blog-details-wrapper">
    #             <div class="special-section content-body">
    #               <p>...</p>
    #               <p>...</p>
    #             </div>
    #           </div>
    #
    #           <!-- Tags inline block -->
    #           <div class="nile_post_apr05-related-inline">
    #             <div class="nile_post_apr05-tags">
    #               <span><a href="...">Tag Name</a></span>
    #             </div>
    #           </div>
    #
    #         </article>
    #       </div>
    #
    #     </main>
    #   </div>
    # ------------------------------------------------------------------ #

    def _scrape_article_detail(self, article_url: str, run: ScrapingRun) -> dict | None:
        """
        Load an individual article page and extract full content.

        Returns a dict with keys:
            full_title, full_content, excerpt, word_count, paragraph_count,
            featured_image_url, image_alt, author_name, author_url,
            published_date_str, tags
        or None on failure.
        """
        for attempt in range(1, 4):  # up to 3 attempts
            try:
                if not self._get_page(article_url):
                    raise TimeoutException("Page load failed")

                # Wait for the article wrapper to appear
                WebDriverWait(self.driver, self.WAIT_TIMEOUT).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "div.nile_post_apr05_body")
                    )
                )

                detail: dict = {}

                # --- Title -------------------------------------------------
                try:
                    title_el = self.driver.find_element(
                        By.CSS_SELECTOR, "h1.nile_post_apr05-title"
                    )
                    detail["full_title"] = self._clean(title_el.text)
                except NoSuchElementException:
                    detail["full_title"] = ""

                # --- Meta (author + date) -----------------------------------
                try:
                    meta_el = self.driver.find_element(
                        By.CSS_SELECTOR, "div.nile_post_apr05-meta"
                    )
                    # Author link inside meta
                    try:
                        author_link = meta_el.find_element(By.TAG_NAME, "a")
                        detail["author_name"] = self._clean(author_link.text)
                        detail["author_url"] = author_link.get_attribute("href") or ""
                    except NoSuchElementException:
                        detail["author_name"] = ""
                        detail["author_url"] = ""

                    # Date: second <a> or plain text after the pipe separator
                    all_links = meta_el.find_elements(By.TAG_NAME, "a")
                    if len(all_links) >= 2:
                        detail["published_date_str"] = self._clean(all_links[1].text)
                    else:
                        # Fallback: strip author name from full meta text
                        full_meta = self._clean(meta_el.text)
                        # e.g. "By Nile Post Editor | Sunday, February 22, 2026"
                        parts = full_meta.split("|")
                        detail["published_date_str"] = (
                            self._clean(parts[1]) if len(parts) >= 2 else ""
                        )
                except NoSuchElementException:
                    detail["author_name"] = ""
                    detail["author_url"] = ""
                    detail["published_date_str"] = ""

                # --- Hero / featured image ----------------------------------
                try:
                    hero_img = self.driver.find_element(
                        By.CSS_SELECTOR, "div.nile_post_apr05-hero img"
                    )
                    detail["featured_image_url"] = (
                        hero_img.get_attribute("src")
                        or hero_img.get_attribute("data-src")
                        or ""
                    )
                    detail["image_alt"] = hero_img.get_attribute("alt") or ""
                except NoSuchElementException:
                    detail["featured_image_url"] = ""
                    detail["image_alt"] = ""

                # --- Styled excerpt box ------------------------------------
                try:
                    excerpt_el = self.driver.find_element(
                        By.CSS_SELECTOR,
                        "article.nile_post_apr05-main-left div.nile_post_apr05-excerpt",
                    )
                    # Same fix — use JS innerText
                    styled_excerpt = self.driver.execute_script(
                        "return arguments[0].innerText.trim();", excerpt_el
                    )
                    detail["styled_excerpt"] = self._clean(styled_excerpt)
                except NoSuchElementException:
                    detail["styled_excerpt"] = ""

                # --- Full body content -------------------------------------
                # Target: .blog-details-wrapper .special-section.content-body p
                # --- Body content ---
                paragraphs: list[str] = []
                try:
                    content_div = self.driver.find_element(
                        By.CSS_SELECTOR,
                        ".blog-details-wrapper .special-section.content-body",
                    )

                    # Use JavaScript to get clean inner text per paragraph,
                    # bypassing whitespace-only text nodes and ad injections
                    paragraphs_raw = self.driver.execute_script("""
                        const container = arguments[0];
                        const paras = container.querySelectorAll('p');
                        const results = [];
                        for (const p of paras) {
                            // Skip paragraphs inside ad divs, embeds, or the tags block
                            if (
                                p.closest('.nile_post_apr05-related-inline') ||
                                p.closest('.wp-block-embed') ||
                                p.closest('script') ||
                                p.closest('style')
                            ) continue;

                            // innerText handles visibility and collapses whitespace correctly
                            const text = (p.innerText || p.textContent || '').trim();
                            if (text.length > 20) {
                                results.push(text);
                            }
                        }
                        return results;
                    """, content_div)

                    paragraphs = paragraphs_raw or []

                except NoSuchElementException:
                    self._log(run, "warning", "Content body not found", article_url)

                detail["full_content"] = "\n\n".join(paragraphs)
                detail["word_count"] = len(detail["full_content"].split())
                detail["paragraph_count"] = len(paragraphs)

                # Use styled excerpt if available, else auto-generate
                detail["excerpt"] = detail.get("styled_excerpt") or self._generate_excerpt(
                    paragraphs
                )

                # --- Tags --------------------------------------------------
                # Tags live inside .nile_post_apr05-related-inline .nile_post_apr05-tags
                tags: list[str] = []
                try:
                    tags_container = self.driver.find_element(
                        By.CSS_SELECTOR,
                        "article.nile_post_apr05-main-left "
                        "div.nile_post_apr05-related-inline "
                        "div.nile_post_apr05-tags",
                    )
                    tag_links = tags_container.find_elements(By.TAG_NAME, "a")
                    for link in tag_links:
                        tag_text = self._clean(link.text)
                        if tag_text:
                            tags.append(tag_text)
                except NoSuchElementException:
                    pass

                detail["tags"] = tags
                return detail

            except (TimeoutException, WebDriverException) as exc:
                if attempt >= 3:
                    self._log(
                        run,
                        "error",
                        f"Failed after 3 attempts: {exc}",
                        article_url,
                    )
                    return None
                wait = 2 ** attempt
                time.sleep(wait)

            except Exception as exc:
                self._log(run, "error", f"Detail parse error: {exc}", article_url)
                return None

        return None

    # ------------------------------------------------------------------ #
    #  Category inference from URL                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _category_from_url(url: str) -> str:
        """
        Infer a category name from the article URL path segment.

        e.g. https://nilepost.co.ug/opinions/322131/... → "Opinions"
             https://nilepost.co.ug/news/321790/...     → "News"
        """
        try:
            path = url.split("nilepost.co.ug")[-1]  # "/opinions/322131/slug"
            segment = path.strip("/").split("/")[0]   # "opinions"
            return segment.replace("-", " ").title() if segment else "Uncategorised"
        except Exception:
            return "Uncategorised"

    # ------------------------------------------------------------------ #
    #  Main entry point                                                    #
    # ------------------------------------------------------------------ #

    def scrape_and_save(
        self,
        get_full_content: bool = True,
        max_articles: int | None = None,
        start_page: int = 1,
        max_pages: int = 1,
        news_url: str | None = None,
    ) -> dict:
        """
        Scrape NilePost articles and persist them via Django ORM.

        Args:
            get_full_content: Whether to visit each article page for full body.
            max_articles:     Hard cap on total articles processed (None = unlimited).
            start_page:       Pagination start (1-indexed).
            max_pages:        How many listing pages to scrape.
            news_url:         Override the source's stored news_url.

        Returns:
            Summary dict with counts for found / added / updated / skipped / errors.
        """
        listing_url = news_url or self.source.news_url
        run = ScrapingRun.objects.create(source=self.source, status="started")

        self._start_driver()

        try:
            self._log(run, "info", f"Selenium scraper started. Base URL: {listing_url}")
            total_processed = 0

            for page_num in range(start_page, start_page + max_pages):

                # Build paginated URL
                # NilePost paginates as /category/page/2/, /category/page/3/, …
                if page_num == 1:
                    page_url = listing_url.rstrip("/") + "/"
                else:
                    page_url = listing_url.rstrip("/") + f"/page/{page_num}/"

                self._log(run, "info", f"Scraping listing page {page_num}: {page_url}")

                article_cards = self._scrape_listing_page(page_url, run)

                if not article_cards:
                    self._log(run, "warning", f"No articles found on page {page_num}. Stopping.")
                    break

                run.articles_found += len(article_cards)
                run.save()
                self._log(run, "info", f"Found {len(article_cards)} articles on page {page_num}")

                for idx, card in enumerate(article_cards):

                    if max_articles and total_processed >= max_articles:
                        self._log(run, "info", f"Reached max_articles limit ({max_articles})")
                        break

                    article_url = card.get("url", "")
                    if not article_url:
                        run.articles_skipped += 1
                        continue

                    try:
                        # --------------------------------------------------
                        # Check for duplicates
                        # --------------------------------------------------
                        external_id = self._external_id_from_url(article_url)

                        existing = (
                            Article.objects.filter(external_id=external_id, source=self.source).first()
                            if external_id
                            else Article.objects.filter(url=article_url).first()
                        )

                        if existing:
                            if get_full_content and not existing.has_full_content:
                                detail = self._scrape_article_detail(article_url, run)
                                if detail:
                                    existing.content = detail["full_content"]
                                    existing.excerpt = detail["excerpt"]
                                    existing.word_count = detail["word_count"]
                                    existing.paragraph_count = detail["paragraph_count"]
                                    existing.image_caption = detail.get("image_alt", "")
                                    existing.has_full_content = True

                                    if detail.get("author_name"):
                                        existing.author = self._get_or_create_author(
                                            detail["author_name"],
                                            detail.get("author_url", ""),
                                        )

                                    existing.save()

                                    existing.tags.clear()
                                    for tag_name in detail.get("tags", []):
                                        tag = self._get_or_create_tag(tag_name)
                                        if tag:
                                            existing.tags.add(tag)

                                    run.articles_updated += 1
                                    self._log(run, "info", f"Updated: {existing.title}")
                                    time.sleep(self.REQUEST_DELAY)
                            else:
                                run.articles_skipped += 1

                            total_processed += 1
                            continue

                        # --------------------------------------------------
                        # New article — build the object
                        # --------------------------------------------------
                        category = self._get_or_create_category(
                            card.get("category") or self._category_from_url(article_url)
                        )
                        author = self._get_or_create_author(
                            card.get("author_name", ""),
                            card.get("author_url", ""),
                        )

                        article = Article(
                            external_id=self._external_id_from_url(article_url),
                            url=article_url,
                            title=card.get("title", ""),
                            featured_image_url=card.get("featured_image", ""),
                            source=self.source,
                            category=category,
                            author=author,
                        )

                        # Parse ISO date from the `content` attribute
                        iso_date = card.get("published_date_content", "")
                        if iso_date:
                            parsed_dt = self._parse_iso_date(iso_date)
                            if parsed_dt:
                                article.published_at = parsed_dt

                        # --------------------------------------------------
                        # Optionally fetch full content
                        # --------------------------------------------------
                        detail = None
                        if get_full_content:
                            detail = self._scrape_article_detail(article_url, run)
                            if detail:
                                article.content = detail["full_content"]
                                article.excerpt = detail["excerpt"]
                                article.word_count = detail["word_count"]
                                article.paragraph_count = detail["paragraph_count"]
                                article.image_caption = detail.get("image_alt", "")
                                article.has_full_content = True

                                if detail.get("featured_image_url"):
                                    article.featured_image_url = detail["featured_image_url"]

                                if detail.get("author_name"):
                                    article.author = self._get_or_create_author(
                                        detail["author_name"],
                                        detail.get("author_url", ""),
                                    )

                                if detail.get("full_title"):
                                    article.title = detail["full_title"]

                        article.save()

                        # Tags
                        tags_to_add = (
                            detail.get("tags", []) if detail else card.get("tags", [])
                        )
                        for tag_name in tags_to_add:
                            tag = self._get_or_create_tag(tag_name)
                            if tag:
                                article.tags.add(tag)

                        run.articles_added += 1
                        total_processed += 1
                        self._log(run, "info", f"Added: {article.title}")
                        time.sleep(self.REQUEST_DELAY)

                    except Exception as exc:
                        run.error_count += 1
                        self._log(
                            run,
                            "error",
                            f"Error on article {idx + 1} (page {page_num}): {exc}",
                            article_url,
                        )
                        continue

                if max_articles and total_processed >= max_articles:
                    break

                # Delay between listing pages
                if page_num < start_page + max_pages - 1:
                    time.sleep(self.PAGE_DELAY)

            # ------------------------------------------------------------------
            # Finalise run
            # ------------------------------------------------------------------
            run.status = "completed"
            run.completed_at = timezone.now()
            run.save()

            summary = (
                f"Done. Added: {run.articles_added}, "
                f"Updated: {run.articles_updated}, "
                f"Skipped: {run.articles_skipped}, "
                f"Errors: {run.error_count}"
            )
            self._log(run, "info", summary)

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
            self._log(run, "error", f"Scraping failed: {exc}")
            raise

        finally:
            self._quit_driver()

    def _external_id_from_url(self, url:str) -> str:
        """
        Extract the numeric post ID from a NilePost URL.
        e.g. https://nilepost.co.ug/News/322081/some-slug → '322081'
        """
        try:
            parts = url.rstrip("/").split("/")
            for part in reversed(parts):
                if part.isdigit():
                    return part
        except Exception:
            pass
        return ""