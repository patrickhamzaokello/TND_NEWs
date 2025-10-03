"""
Management command for cleaning up stale and orphaned videos

Usage:
    python manage.py cleanup_videos --hours 24
    python manage.py cleanup_videos --orphaned
    python manage.py cleanup_videos --all
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from tnd_apps.tndvideo.utils import cleanup_failed_uploads, cleanup_orphaned_files


class Command(BaseCommand):
    help = 'Clean up stale video uploads and orphaned files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Hours after which to consider uploads stale (default: 24)'
        )

        parser.add_argument(
            '--orphaned',
            action='store_true',
            help='Clean up orphaned files without database records'
        )

        parser.add_argument(
            '--all',
            action='store_true',
            help='Run all cleanup tasks'
        )

        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be cleaned up without actually deleting'
        )

    def handle(self, *args, **options):
        hours = options['hours']
        orphaned = options['orphaned']
        run_all = options['all']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No files will be deleted')
            )

        total_cleaned = 0

        # Clean up stale uploads
        if not orphaned or run_all:
            self.stdout.write(f'Cleaning up stale uploads (older than {hours} hours)...')

            if not dry_run:
                stale_count = cleanup_failed_uploads(hours=hours)
                total_cleaned += stale_count
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Cleaned up {stale_count} stale videos')
                )
            else:
                self.stdout.write('Would clean up stale uploads')

        # Clean up orphaned files
        if orphaned or run_all:
            self.stdout.write('Cleaning up orphaned files...')

            if not dry_run:
                orphaned_count = cleanup_orphaned_files()
                total_cleaned += orphaned_count
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Cleaned up {orphaned_count} orphaned directories')
                )
            else:
                self.stdout.write('Would clean up orphaned files')

        # Summary
        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'\n✓ Total items cleaned: {total_cleaned}')
            )
        else:
            self.stdout.write(
                self.style.WARNING('\nDry run completed - no changes made')
            )