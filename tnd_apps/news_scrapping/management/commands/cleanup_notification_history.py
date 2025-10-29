from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from tnd_apps.news_scrapping.models import (
    UserNotification, ArticleNotificationHistory
)


class Command(BaseCommand):
    help = 'Clean up old notification records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Delete notifications older than this many days (default: 30)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']

        cutoff_date = timezone.now() - timedelta(days=days)

        # Count notifications to delete
        notifications_to_delete = UserNotification.objects.filter(
            sent_at__lt=cutoff_date
        )
        notification_count = notifications_to_delete.count()

        # Count history to delete
        history_to_delete = ArticleNotificationHistory.objects.filter(
            sent_at__lt=cutoff_date
        )
        history_count = history_to_delete.count()

        self.stdout.write(f"Found {notification_count} notifications older than {days} days")
        self.stdout.write(f"Found {history_count} history entries older than {days} days")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - Nothing deleted"))
        else:
            # Delete
            notifications_to_delete.delete()
            history_to_delete.delete()

            self.stdout.write(self.style.SUCCESS(
                f"Deleted {notification_count} notifications and {history_count} history entries"
            ))