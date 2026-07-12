"""
Wipe all story data and rebuild from scratch with the semantic story engine.

Usage:
    python manage.py rebuild_stories                  # full wipe + rebuild
    python manage.py rebuild_stories --resume         # continue without deleting
    python manage.py rebuild_stories --keep-embeddings  # wipe stories but reuse embeddings
"""

from django.core.management.base import BaseCommand

from tnd_apps.newsintelligence.models import ArticleEnrichment, StoryCluster
from tnd_apps.newsintelligence.story_engine import process_new_articles


class Command(BaseCommand):
    help = 'Clear all story clusters and rebuild them with the semantic story engine.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--resume', action='store_true',
            help='Skip deletion — just continue processing unfinished articles',
        )
        parser.add_argument(
            '--keep-embeddings', action='store_true',
            help='Keep existing article embeddings (skip re-embedding cost)',
        )
        parser.add_argument('--batch-size', type=int, default=200)

    def handle(self, *args, **options):
        if not options['resume']:
            count = StoryCluster.objects.count()
            StoryCluster.objects.all().delete()
            self.stdout.write(self.style.WARNING(
                f'Deleted {count} story clusters '
                '(cascade: links, timelines, perspectives, alerts, versions, relations)'
            ))

            if not options['keep_embeddings']:
                cleared = ArticleEnrichment.objects.exclude(
                    embedding__isnull=True
                ).update(embedding=None, embedded_at=None)
                self.stdout.write(self.style.WARNING(
                    f'Cleared {cleared} embeddings (will regenerate from article bodies)'
                ))

        total_articles = ArticleEnrichment.objects.filter(status='completed').count()
        self.stdout.write(f'\nTotal enriched articles to process: {total_articles}\n')

        batch = options['batch_size']
        pass_num = 0
        totals = {'embedded': 0, 'assigned': 0, 'stories_created': 0, 'synthesized': 0}

        while True:
            pass_num += 1
            result = process_new_articles(batch_size=batch)

            for key in totals:
                totals[key] += result.get(key, 0)

            done = totals['assigned']
            pct = (done / total_articles * 100) if total_articles else 100
            self.stdout.write(
                f'[pass {pass_num:>3}] '
                f'embedded +{result["embedded"]:<4} '
                f'assigned +{result["assigned"]:<4} '
                f'new stories +{result["stories_created"]:<4} '
                f'synthesized +{result["synthesized"]:<4} '
                f'| progress: {done}/{total_articles} ({pct:.1f}%)'
            )

            if result['embedded'] == 0 and result['assigned'] == 0:
                break

        stories = StoryCluster.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {totals["assigned"]} articles assigned into {stories} stories '
            f'({totals["synthesized"]} synthesized).'
        ))
