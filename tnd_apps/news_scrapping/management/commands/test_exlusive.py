"""
Django management command for running the Exclusive.co.ug scraper

Usage:
    python manage.py scrape_exclusive --full-content --max-articles 50
    python manage.py scrape_exclusive --pages 3
    python manage.py scrape_exclusive --help
"""

from django.core.management.base import BaseCommand, CommandError

from tnd_apps.news_scrapping.exclusive_bizz_scrapper import ExclusiveCoUgScraper


class Command(BaseCommand):
    help = 'Scrape articles from Exclusive.co.ug news website'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-articles',
            type=int,
            default=None,
            help='Maximum number of articles to scrape (default: all)',
        )

        parser.add_argument(
            '--pages',
            type=int,
            default=1,
            help='Number of pages to scrape (default: 1)',
        )

        parser.add_argument(
            '--start-page',
            type=int,
            default=1,
            help='Page number to start from (default: 1)',
        )

        parser.add_argument(
            '--full-content',
            action='store_true',
            help='Fetch full article content (slower but more complete)',
        )

        parser.add_argument(
            '--no-full-content',
            action='store_true',
            help='Skip fetching full article content (faster)',
        )

    def handle(self, *args, **options):
        max_articles = options['max_articles']
        pages = options['pages']
        start_page = options['start_page']

        # Determine whether to fetch full content
        if options['no_full_content']:
            get_full_content = False
        else:
            get_full_content = options['full_content'] or True

        self.stdout.write(self.style.SUCCESS('Starting Exclusive.co.ug scraper...'))
        self.stdout.write(f'  Max articles: {max_articles or "All"}')
        self.stdout.write(f'  Pages to scrape: {pages}')
        self.stdout.write(f'  Starting from page: {start_page}')
        self.stdout.write(f'  Get full content: {get_full_content}')
        self.stdout.write('')

        try:
            scraper = ExclusiveCoUgScraper()

            result = scraper.scrape_and_save(
                get_full_content=get_full_content,
                max_articles=max_articles,
                start_page=start_page,
                max_pages=pages
            )

            self.stdout.write(self.style.SUCCESS('\nScraping completed successfully!'))
            self.stdout.write(f'  Run ID: {result["run_id"]}')
            self.stdout.write(f'  Articles found: {result["articles_found"]}')
            self.stdout.write(f'  Articles added: {result["articles_added"]}')
            self.stdout.write(f'  Articles updated: {result["articles_updated"]}')
            self.stdout.write(f'  Articles skipped: {result["articles_skipped"]}')
            self.stdout.write(f'  Errors: {result["errors"]}')
            self.stdout.write(f'  Duration: {result["duration"]:.2f} seconds')

        except Exception as e:
            raise CommandError(f'Scraping failed: {str(e)}')