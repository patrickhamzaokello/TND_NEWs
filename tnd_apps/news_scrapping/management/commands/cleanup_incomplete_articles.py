from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from tnd_apps.news_scrapping.models import Article


class Command(BaseCommand):
    help = "Delete scraped article rows that do not have full content."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-hours",
            type=int,
            default=0,
            help="Only delete rows scraped at least this many hours ago. Default: 0.",
        )
        parser.add_argument(
            "--source",
            type=str,
            default="",
            help="Optional NewsSource.name filter.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum rows to delete. Default: 0 means no limit.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Actually delete rows. Without this flag the command only prints a dry-run summary.",
        )

    def handle(self, *args, **options):
        older_than_hours = options["older_than_hours"]
        if older_than_hours < 0:
            raise CommandError("--older-than-hours cannot be negative")

        queryset = Article.objects.filter(has_full_content=False)

        if older_than_hours:
            cutoff = timezone.now() - timedelta(hours=older_than_hours)
            queryset = queryset.filter(scraped_at__lte=cutoff)

        source = options["source"].strip()
        if source:
            queryset = queryset.filter(source__name=source)

        by_source = (
            queryset.values("source__name")
            .annotate(total=Count("id"))
            .order_by("source__name")
        )
        total = queryset.count()

        self.stdout.write(f"Incomplete articles matched: {total}")
        for row in by_source:
            self.stdout.write(f"  {row['source__name'] or 'Unknown'}: {row['total']}")

        if not options["delete"]:
            self.stdout.write(self.style.WARNING("Dry run only. Re-run with --delete to remove these rows."))
            return

        limit = options["limit"]
        if limit:
            ids = list(queryset.order_by("scraped_at").values_list("id", flat=True)[:limit])
            queryset = Article.objects.filter(id__in=ids)

        deleted_count, deleted_by_model = queryset.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted_count} objects."))
        for model_name, count in sorted(deleted_by_model.items()):
            self.stdout.write(f"  {model_name}: {count}")
