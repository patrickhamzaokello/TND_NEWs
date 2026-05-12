from django.core.management.base import BaseCommand, CommandError

from tnd_apps.news_scrapping.ubc_scrapper import UBCScraper


class Command(BaseCommand):
    help = "Scrape articles from UBC"

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-articles",
            type=int,
            default=None,
            help="Maximum number of articles to scrape.",
        )
        parser.add_argument(
            "--pages",
            type=int,
            default=1,
            help="Number of listing pages to scrape.",
        )
        parser.add_argument(
            "--start-page",
            type=int,
            default=1,
            help="Listing page number to start from.",
        )
        parser.add_argument(
            "--news-url",
            default=None,
            help="Override the UBC listing URL.",
        )
        parser.add_argument(
            "--no-full-content",
            action="store_true",
            help="Skip fetching article detail pages.",
        )

    def handle(self, *args, **options):
        get_full_content = not options["no_full_content"]
        self.stdout.write(self.style.SUCCESS("Starting UBC scraper..."))
        self.stdout.write(f"  Max articles: {options['max_articles'] or 'All'}")
        self.stdout.write(f"  Pages: {options['pages']}")
        self.stdout.write(f"  Start page: {options['start_page']}")
        self.stdout.write(f"  Full content: {get_full_content}")

        try:
            scraper = UBCScraper()
            result = scraper.scrape_and_save(
                get_full_content=get_full_content,
                max_articles=options["max_articles"],
                start_page=options["start_page"],
                max_pages=options["pages"],
                news_url=options["news_url"],
            )
        except Exception as exc:
            raise CommandError(f"UBC scraping failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("Scraping completed successfully."))
        self.stdout.write(f"  Run ID: {result['run_id']}")
        self.stdout.write(f"  Articles found: {result['articles_found']}")
        self.stdout.write(f"  Articles added: {result['articles_added']}")
        self.stdout.write(f"  Articles updated: {result['articles_updated']}")
        self.stdout.write(f"  Articles skipped: {result['articles_skipped']}")
        self.stdout.write(f"  Errors: {result['errors']}")
        self.stdout.write(f"  Duration: {result['duration']:.2f} seconds")
