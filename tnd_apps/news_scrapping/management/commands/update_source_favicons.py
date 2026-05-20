from django.core.management.base import BaseCommand

from tnd_apps.news_scrapping.models import NewsSource
from tnd_apps.news_scrapping.source_favicons import SourceFaviconResolver


class Command(BaseCommand):
    help = "Fetch and store favicon URLs for news sources."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-id",
            type=int,
            default=None,
            help="Update one source by ID.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Refresh sources even when favicon_url is already set.",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Include inactive sources.",
        )

    def handle(self, *args, **options):
        queryset = NewsSource.objects.all().order_by("name")
        if options["source_id"]:
            queryset = queryset.filter(id=options["source_id"])
        if not options["include_inactive"]:
            queryset = queryset.filter(is_active=True)
        if not options["all"]:
            queryset = queryset.filter(favicon_url="")

        resolver = SourceFaviconResolver()
        updated = 0
        missing = 0

        for source in queryset:
            favicon_url = resolver.resolve(source)
            if favicon_url:
                source.favicon_url = favicon_url
                source.save(update_fields=["favicon_url"])
                updated += 1
                self.stdout.write(self.style.SUCCESS(f"{source.name}: {favicon_url}"))
            else:
                missing += 1
                self.stdout.write(self.style.WARNING(f"{source.name}: no favicon found"))

        self.stdout.write(
            self.style.SUCCESS(f"Done. Updated: {updated}. Missing: {missing}.")
        )
