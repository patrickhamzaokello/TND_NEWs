"""
Django management command: scrape_nilepost
==========================================

Dry-run the NilePost Selenium scraper without writing anything to the database.

Usage examples
--------------

# Dry run — prints what would be scraped, saves nothing
python manage.py scrape_nilepost --dry-run

# Dry run, 2 pages, no full article content fetch
python manage.py scrape_nilepost --dry-run --pages 2 --no-full-content

# Real run — actually saves to DB
python manage.py scrape_nilepost --pages 1 --max-articles 10

# Real run with a custom listing URL
python manage.py scrape_nilepost --url https://nilepost.co.ug/news --pages 1

Place this file at:
    <your_app>/management/commands/scrape_nilepost.py

Make sure the management/commands/ directories each contain an empty __init__.py.
"""

import textwrap
from django.core.management.base import BaseCommand, CommandError

# ── colour helpers (work on any terminal; degrade gracefully) ──────────────
try:
    from django.utils.termcolors import colorize
    GREEN  = lambda s: colorize(s, fg="green")
    YELLOW = lambda s: colorize(s, fg="yellow")
    CYAN   = lambda s: colorize(s, fg="cyan")
    RED    = lambda s: colorize(s, fg="red")
    BOLD   = lambda s: colorize(s, opts=("bold",))
except Exception:
    GREEN = YELLOW = CYAN = RED = BOLD = lambda s: s   # no-op fallback


class Command(BaseCommand):
    help = "Scrape NilePost articles. Use --dry-run to preview without saving."

    # ------------------------------------------------------------------ #
    #  Argument definition                                                 #
    # ------------------------------------------------------------------ #

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help=(
                "Preview scraping results without writing anything to the database. "
                "Article cards from the listing page are fetched and printed; "
                "full article content is also fetched (unless --no-full-content) "
                "but nothing is saved."
            ),
        )
        parser.add_argument(
            "--url",
            type=str,
            default=None,
            metavar="URL",
            help=(
                "Listing page URL to scrape. "
                "Defaults to the URL stored on the NewsSource model."
            ),
        )
        parser.add_argument(
            "--pages",
            type=int,
            default=1,
            metavar="N",
            help="Number of listing pages to scrape (default: 1).",
        )
        parser.add_argument(
            "--start-page",
            type=int,
            default=1,
            metavar="N",
            help="Page number to start from (default: 1).",
        )
        parser.add_argument(
            "--max-articles",
            type=int,
            default=None,
            metavar="N",
            help="Maximum total articles to process across all pages (default: unlimited).",
        )
        parser.add_argument(
            "--no-full-content",
            action="store_true",
            default=False,
            help=(
                "Skip fetching each article's detail page. "
                "Only listing-page metadata will be shown / saved."
            ),
        )
        parser.add_argument(
            "--source",
            type=str,
            default="NilePost",
            metavar="NAME",
            help="NewsSource.name to use (default: 'NilePost').",
        )
        parser.add_argument(
            "--show-browser",
            action="store_true",
            default=False,
            help="Run Chrome in non-headless mode (shows the browser window). Useful for debugging.",
        )

    # ------------------------------------------------------------------ #
    #  Handle                                                              #
    # ------------------------------------------------------------------ #

    def handle(self, *args, **options):
        dry_run         = options["dry_run"]
        listing_url     = options["url"]
        max_pages       = options["pages"]
        start_page      = options["start_page"]
        max_articles    = options["max_articles"]
        get_full        = not options["no_full_content"]
        source_name     = options["source"]
        headless        = not options["show_browser"]

        # Lazy import so the command can at least be *registered* even if
        # selenium isn't installed in a given environment.
        try:
            from tnd_apps.news_scrapping.nilepost_scrapper import NilePostScraper  # adjust import path
        except ImportError as exc:
            raise CommandError(f"Could not import NilePostScraper: {exc}")

        # ── Banner ─────────────────────────────────────────────────────
        mode_label = BOLD(YELLOW("DRY RUN")) if dry_run else BOLD(GREEN("LIVE RUN"))
        self.stdout.write("")
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write(f"  NilePost Scraper  [{mode_label}]")
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write(f"  Source name   : {CYAN(source_name)}")
        self.stdout.write(f"  Listing URL   : {CYAN(listing_url or '<from NewsSource model>')}")
        self.stdout.write(f"  Pages         : {start_page} → {start_page + max_pages - 1}")
        self.stdout.write(f"  Max articles  : {max_articles or 'unlimited'}")
        self.stdout.write(f"  Full content  : {'yes' if get_full else 'no'}")
        self.stdout.write(f"  Headless      : {'yes' if headless else 'no'}")
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write("")

        scraper = NilePostScraper(source_name=source_name, headless=headless)

        # ── DRY RUN ────────────────────────────────────────────────────
        if dry_run:
            self._dry_run(
                scraper,
                listing_url=listing_url,
                start_page=start_page,
                max_pages=max_pages,
                max_articles=max_articles,
                get_full=get_full,
            )
            return

        # ── LIVE RUN ───────────────────────────────────────────────────
        self.stdout.write("Starting live scrape …")
        try:
            result = scraper.scrape_and_save(
                get_full_content=get_full,
                max_articles=max_articles,
                start_page=start_page,
                max_pages=max_pages,
                news_url=listing_url,
            )
        except Exception as exc:
            raise CommandError(f"Scraper failed: {exc}")

        self.stdout.write("")
        self.stdout.write(BOLD(GREEN("✓ Scrape complete")))
        self.stdout.write(f"  Run ID          : {result['run_id']}")
        self.stdout.write(f"  Articles found  : {result['articles_found']}")
        self.stdout.write(f"  Added           : {GREEN(str(result['articles_added']))}")
        self.stdout.write(f"  Updated         : {CYAN(str(result['articles_updated']))}")
        self.stdout.write(f"  Skipped         : {result['articles_skipped']}")
        self.stdout.write(f"  Errors          : {RED(str(result['errors'])) if result['errors'] else '0'}")
        self.stdout.write(f"  Duration        : {result.get('duration', '?')}s")
        self.stdout.write("")

    # ------------------------------------------------------------------ #
    #  Dry-run logic                                                       #
    # ------------------------------------------------------------------ #

    def _dry_run(self, scraper, listing_url, start_page, max_pages, max_articles, get_full):
        """
        Fetch articles using the scraper internals but skip all DB writes.
        Prints a structured preview of what would be saved.
        """
        from django.db import transaction

        # We still need a driver
        scraper._start_driver()

        # Create a throw-away in-memory run object (not saved)
        class FakeRun:
            articles_found = 0
            articles_skipped = 0
            error_count = 0

            def save(self):
                pass

        fake_run = FakeRun()

        total_processed = 0
        grand_total_cards = 0

        try:
            for page_num in range(start_page, start_page + max_pages):
                if page_num == 1:
                    page_url = (listing_url or scraper.source.news_url).rstrip("/") + "/"
                else:
                    base = listing_url or scraper.source.news_url
                    page_url = base.rstrip("/") + f"/page/{page_num}/"

                self.stdout.write(BOLD(f"── Page {page_num}: {page_url}"))
                self.stdout.write("")

                cards = scraper._scrape_listing_page(page_url, fake_run)

                if not cards:
                    self.stdout.write(YELLOW("  No articles found on this page."))
                    break

                grand_total_cards += len(cards)
                self.stdout.write(f"  Found {BOLD(str(len(cards)))} article cards\n")

                for idx, card in enumerate(cards, start=1):
                    if max_articles and total_processed >= max_articles:
                        self.stdout.write(YELLOW(f"\n  Reached --max-articles={max_articles}, stopping."))
                        break

                    url   = card.get("url", "N/A")
                    title = card.get("title", "N/A")

                    # Check if already in DB (read-only, no write)
                    from tnd_apps.news_scrapping.models import Article  # adjust import path
                    exists = Article.objects.filter(url=url).exists()
                    status = YELLOW("EXISTS") if exists else GREEN("NEW")

                    self.stdout.write(
                        f"  [{idx:>3}] {status}  {BOLD(title)}"
                    )
                    self.stdout.write(f"        URL    : {CYAN(url)}")
                    self.stdout.write(
                        f"        Author : {card.get('author_name', '—')}  "
                        f"| Date : {card.get('published_date_content') or card.get('published_date_text', '—')}"
                    )

                    # Optionally preview detail page
                    if get_full and not exists:
                        self.stdout.write("        Fetching article detail …")
                        detail = scraper._scrape_article_detail(url, fake_run)
                        if detail:
                            word_count = detail.get("word_count", 0)
                            excerpt    = detail.get("excerpt", "")
                            tags       = detail.get("tags", [])
                            author_det = detail.get("author_name", "—")
                            pub_str    = detail.get("published_date_str", "—")

                            short_excerpt = textwrap.shorten(excerpt, width=120, placeholder="…")
                            self.stdout.write(f"        Full title : {detail.get('full_title', '—')}")
                            self.stdout.write(f"        Author (detail) : {author_det}")
                            self.stdout.write(f"        Published  : {pub_str}")
                            self.stdout.write(f"        Words      : {word_count}")
                            self.stdout.write(f"        Tags       : {', '.join(tags) or '—'}")
                            self.stdout.write(f"        Excerpt    : {short_excerpt}")
                        else:
                            self.stdout.write(RED("        ✗ Could not fetch article detail."))

                    self.stdout.write("")
                    total_processed += 1

                if max_articles and total_processed >= max_articles:
                    break

        finally:
            scraper._quit_driver()

        # Summary
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write(BOLD(YELLOW("DRY RUN COMPLETE — nothing was saved.")))
        self.stdout.write(f"  Total listing cards found : {grand_total_cards}")
        self.stdout.write(f"  Articles previewed        : {total_processed}")
        self.stdout.write(f"  Errors / warnings         : {fake_run.error_count}")
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write("")