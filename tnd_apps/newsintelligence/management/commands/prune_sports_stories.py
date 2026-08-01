"""
One-off cleanup: remove all sports-themed story clusters.

Sports coverage overwhelmingly produced single-source, one-off stories that
add clutter to the stories feed (web + mobile) without being genuinely
significant, while costing embedding + LLM adjudication calls to maintain.
Sports was excluded from the story pipeline going forward (see
story_engine.EXCLUDED_STORY_THEMES) — this command clears out what already
exists so the feeds reflect that immediately rather than waiting for the
excluded stories to age out naturally.

Article-level enrichment (summaries, key facts) for sports articles is left
untouched — only the StoryCluster rows (and their cascaded links, timeline
events, perspectives, versions, relations) are removed.

Usage:
    python manage.py prune_sports_stories --dry-run
    python manage.py prune_sports_stories
"""

from django.core.management.base import BaseCommand

from tnd_apps.newsintelligence.models import StoryCluster


class Command(BaseCommand):
    help = "Delete all sports-themed story clusters (cleanup after excluding sports from the story pipeline)."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        qs = StoryCluster.objects.filter(primary_theme__iexact='sports')
        count = qs.count()

        if options['dry_run']:
            for cluster in qs.order_by('-last_seen_at')[:50]:
                articles = cluster.cluster_articles.count()
                self.stdout.write(f'  would delete: [{articles} article(s)] {cluster.title[:80]}')
            if count > 50:
                self.stdout.write(f'  ... and {count - 50} more')
            self.stdout.write(self.style.WARNING(f'Would delete {count} sports story clusters.'))
            return

        qs.delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} sports story clusters.'))
