"""
One-time, interactive Google Drive authorization for backups.

Run this LOCALLY (it opens a browser) — not on the headless server.
See tnd_apps/news_scrapping/backup_service.py module docstring for full setup.
"""

from django.core.management.base import BaseCommand

from tnd_apps.news_scrapping.backup_service import run_oauth_flow


class Command(BaseCommand):
    help = 'One-time OAuth authorization for Google Drive backups (run locally, opens a browser).'

    def handle(self, *args, **options):
        self.stdout.write('Opening browser for Google account consent...')
        run_oauth_flow()
        self.stdout.write(self.style.SUCCESS(
            'Authorized. gdrive_token.json was written to the project root — '
            'copy it to the server (or point GDRIVE_TOKEN_FILE at it) before running backups there.'
        ))
