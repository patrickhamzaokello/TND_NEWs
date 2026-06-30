"""
Django management command: scrape_observer
==========================================

Scrape The Observer (https://observer.ug) — a leading Ugandan news outlet.

Usage examples
--------------

# Dry run — prints what would be scraped, saves nothing
python manage.py scrape_observer --dry-run

# Dry run on the business section, 2 pages
python manage.py scrape_observer --dry-run --section business --pages 2

# Live run — news section, 1 page, up to 20 articles
python manage.py scrape_observer --section news --max-articles 20

# Live run — business section, 2 pages
python manage.py scrape_observer --section business --pages 2

# Live run with a custom listing URL
python manage.py scrape_observer --url https://observer.ug/viewpoint --pages 1

# Skip full-content fetch (listing metadata only)
python manage.py scrape_observer --no-full-content --section news
"""

import textwrap
from django.core.management.base import BaseCommand, CommandError

try:
    from django.utils.termcolors import colorize
    GREEN  = lambda s: colorize(s, fg="green")
    YELLOW = lambda s: colorize(s, fg="yellow")
    CYAN   = lambda s: colorize(s, fg="cyan")
    RED    = lambda s: colorize(s, fg="red")
    BOLD   = lambda s: colorize(s, opts=("bold",))
except Exception:
    GREEN = YELLOW = CYAN = RED = BOLD = lambda s: s


OBSERVER_SECTIONS = {
    "news":                    "https://observer.ug/news",
    "business":                "https://observer.ug/business",
    "education":               "https://observer.ug/education",
    "sports":                  "https://observer.ug/sports",
    "viewpoint":               "https://observer.ug/viewpoint",
    "lifestyle-entertainment": "https://observer.ug/lifestyle-entertainment",
    "technology":              "https://observer.ug/technology",
}


class Command(BaseCommand):
    help = "Scrape The Observer Uganda articles. Use --dry-run to preview without saving."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help=(
                "Preview scraping results without writing anything to the database. "
                "Listing cards are fetched and printed; nothing is saved."
            ),
        )
        parser.add_argument(
            "--section",
            type=str,
            default="news",
            choices=list(OBSERVER_SECTIONS.keys()),
            metavar="SECTION",
            help=(
                f"Observer section to scrape. "
                f"Choices: {', '.join(OBSERVER_SECTIONS)}. "
                f"Default: news."
            ),
        )
        parser.add_argument(
            "--url",
            type=str,
            default=None,
            metavar="URL",
            help="Override the listing URL. Defaults to the URL for --section.",
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
            help="Maximum total articles to process (default: unlimited).",
        )
        parser.add_argument(
            "--no-full-content",
            action="store_true",
            default=False,
            help="Skip fetching each article's detail page.",
        )
        parser.add_argument(
            "--source",
            type=str,
            default="The Observer",
            metavar="NAME",
            help="NewsSource.name to use (default: 'The Observer').",
        )
        parser.add_argument(
            "--show-browser",
            action="store_true",
            default=False,
            help="Run Chrome in non-headless mode. Useful for debugging.",
        )

    def handle(self, *args, **options):
        dry_run      = options["dry_run"]
        section      = options["section"]
        listing_url  = options["url"] or OBSERVER_SECTIONS[section]
        max_pages    = options["pages"]
        start_page   = options["start_page"]
        max_articles = options["max_articles"]
        get_full     = not options["no_full_content"]
        source_name  = options["source"]
        headless     = not options["show_browser"]

        try:
            from tnd_apps.news_scrapping.observer_scrapper import ObserverUgScraper
        except ImportError as exc:
            raise CommandError(f"Could not import ObserverUgScraper: {exc}")

        mode_label = BOLD(YELLOW("DRY RUN")) if dry_run else BOLD(GREEN("LIVE RUN"))
        self.stdout.write("")
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write(f"  The Observer UG Scraper  [{mode_label}]")
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write(f"  Source name   : {CYAN(source_name)}")
        self.stdout.write(f"  Section       : {CYAN(section)}")
        self.stdout.write(f"  Listing URL   : {CYAN(listing_url)}")
        self.stdout.write(f"  Pages         : {start_page} → {start_page + max_pages - 1}")
        self.stdout.write(f"  Max articles  : {max_articles or 'unlimited'}")
        self.stdout.write(f"  Full content  : {'yes' if get_full else 'no'}")
        self.stdout.write(f"  Headless      : {'yes' if headless else 'no'}")
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write("")

        scraper = ObserverUgScraper(source_name=source_name, headless=headless)

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
        self.stdout.write(
            f"  Errors          : {RED(str(result['errors'])) if result['errors'] else '0'}"
        )
        self.stdout.write(f"  Duration        : {result.get('duration', '?')}s")
        self.stdout.write("")

    def _dry_run(self, scraper, listing_url, start_page, max_pages, max_articles, get_full):
        scraper._start_driver()

        class FakeRun:
            articles_found = 0
            articles_skipped = 0
            error_count = 0

            def save(self, **kwargs):
                pass

        fake_run = FakeRun()
        total_processed = 0
        grand_total = 0

        try:
            for page_num in range(start_page, start_page + max_pages):
                page_url = (
                    listing_url.rstrip("/") + "/"
                    if page_num == 1
                    else listing_url.rstrip("/") + f"/page/{page_num}/"
                )

                self.stdout.write(BOLD(f"── Page {page_num}: {page_url}"))
                self.stdout.write("")

                cards = scraper._scrape_listing_page(page_url, fake_run)

                if not cards:
                    self.stdout.write(YELLOW("  No articles found on this page."))
                    break

                grand_total += len(cards)
                self.stdout.write(f"  Found {BOLD(str(len(cards)))} article cards\n")

                from tnd_apps.news_scrapping.models import Article

                for idx, card in enumerate(cards, start=1):
                    if max_articles and total_processed >= max_articles:
                        self.stdout.write(YELLOW(f"\n  Reached --max-articles={max_articles}, stopping."))
                        break

                    url   = card.get("url", "N/A")
                    title = card.get("title", "N/A")
                    exists = Article.objects.filter(url=url).exists()
                    status = YELLOW("EXISTS") if exists else GREEN("NEW")

                    self.stdout.write(f"  [{idx:>3}] {status}  {BOLD(title)}")
                    self.stdout.write(f"        URL      : {CYAN(url)}")
                    self.stdout.write(
                        f"        Author   : {card.get('author_name', '—')}  "
                        f"| Date : {card.get('published_date_str', '—')}"
                    )
                    self.stdout.write(f"        Category : {card.get('category', '—')}")

                    if get_full and not exists:
                        self.stdout.write("        Fetching article detail …")
                        detail = scraper._scrape_article_detail(url, fake_run)
                        if detail:
                            short_excerpt = textwrap.shorten(
                                detail.get("excerpt", ""), width=120, placeholder="…"
                            )
                            self.stdout.write(f"        Full title : {detail.get('full_title', '—')}")
                            self.stdout.write(f"        Author det : {detail.get('author_name', '—')}")
                            self.stdout.write(f"        Published  : {detail.get('published_date_str', '—')}")
                            self.stdout.write(f"        Words      : {detail.get('word_count', 0)}")
                            self.stdout.write(f"        Category   : {detail.get('category', '—')}")
                            self.stdout.write(f"        Tags       : {', '.join(detail.get('tags', [])) or '—'}")
                            self.stdout.write(f"        Excerpt    : {short_excerpt}")
                        else:
                            self.stdout.write(RED("        ✗ Could not fetch article detail."))

                    self.stdout.write("")
                    total_processed += 1

                if max_articles and total_processed >= max_articles:
                    break

        finally:
            scraper._quit_driver()

        self.stdout.write(BOLD("=" * 60))
        self.stdout.write(BOLD(YELLOW("DRY RUN COMPLETE — nothing was saved.")))
        self.stdout.write(f"  Total listing cards found : {grand_total}")
        self.stdout.write(f"  Articles previewed        : {total_processed}")
        self.stdout.write(f"  Errors / warnings         : {fake_run.error_count}")
        self.stdout.write(BOLD("=" * 60))
        self.stdout.write("")
