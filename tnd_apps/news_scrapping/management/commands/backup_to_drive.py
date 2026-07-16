"""
Back up the database (and optionally media files) and upload to Google Drive.

Usage:
    python manage.py backup_to_drive
    python manage.py backup_to_drive --skip-media
    python manage.py backup_to_drive --keep 30
    python manage.py backup_to_drive --keep-local

Requires a one-time `python manage.py gdrive_authorize` (run locally) first —
see tnd_apps/news_scrapping/backup_service.py for full setup instructions.
"""

from django.core.management.base import BaseCommand, CommandError

from tnd_apps.news_scrapping.backup_service import run_backup


class Command(BaseCommand):
    help = 'Dump the database (+media) into a zip and upload it to Google Drive.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-media', action='store_true',
            help='Back up the database only, skip the media/ directory.',
        )
        parser.add_argument(
            '--keep', type=int, default=14,
            help='Number of most recent backups to retain on Drive (0 = keep all). Default: 14.',
        )
        parser.add_argument(
            '--keep-local', action='store_true',
            help='Keep the local zip file after upload instead of deleting it.',
        )

    def handle(self, *args, **options):
        try:
            result = run_backup(
                include_media=not options['skip_media'],
                keep=options['keep'],
                keep_local=options['keep_local'],
            )
        except Exception as exc:
            raise CommandError(f'Backup failed: {exc}') from exc

        self.stdout.write(self.style.SUCCESS(
            f"Backup complete: {result['zip_name']} ({result['zip_size_mb']} MB, "
            f"{result['file_count']} files)"
        ))
        self.stdout.write(f"Drive link: {result['drive_link']}")
        if result['pruned']:
            self.stdout.write(f"Pruned {result['pruned']} old backup(s) on Drive")
        if result['local_path']:
            self.stdout.write(f"Local copy kept at: {result['local_path']}")
