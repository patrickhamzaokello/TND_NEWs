"""
Management command: python manage.py enrich_articles

Options:
  --batch-size N     How many articles to process (default: 50)
  --retry-failed     Retry previously failed enrichments instead
  --digest           Generate today's daily digest
  --digest-date      Generate digest for a specific date (YYYY-MM-DD)
  --stats            Print pipeline statistics and exit
  --dry-run          Show what would be processed without calling the API
"""

import logging
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from tnd_apps.newsintelligence.models import ArticleEnrichment, EnrichmentRun
from tnd_apps.newsintelligence.services import EnrichmentService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run the news enrichment pipeline'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Max articles to process per run (default: 50)',
        )
        parser.add_argument(
            '--retry-failed',
            action='store_true',
            help='Retry previously failed enrichments',
        )
        parser.add_argument(
            '--digest',
            action='store_true',
            help="Generate the daily digest for yesterday",
        )
        parser.add_argument(
            '--digest-date',
            type=str,
            help="Generate digest for specific date, e.g. 2026-02-14",
        )
        parser.add_argument(
            '--stats',
            action='store_true',
            help='Print pipeline stats and exit',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show pending articles without calling the API',
        )

    def handle(self, *args, **options):
        service = EnrichmentService(batch_size=options['batch_size'])

        # ── Stats ──────────────────────────────────────────────────────────
        if options['stats']:
            self._print_stats(service)
            return

        # ── Dry run ────────────────────────────────────────────────────────
        if options['dry_run']:
            self._print_dry_run(service)
            return

        # ── Retry failed ───────────────────────────────────────────────────
        if options['retry_failed']:
            self.stdout.write('Retrying failed enrichments...')
            run = service.run_retry_failed()
            self.stdout.write(self.style.SUCCESS(
                f'Done. Processed: {run.articles_processed} | Failed: {run.articles_failed}'
            ))
            return

        # ── Daily digest ───────────────────────────────────────────────────
        if options['digest'] or options['digest_date']:
            target_date = None
            if options['digest_date']:
                try:
                    target_date = datetime.strptime(
                        options['digest_date'], '%Y-%m-%d'
                    ).date()
                except ValueError:
                    raise CommandError('--digest-date must be in YYYY-MM-DD format')

            self.stdout.write(f'Generating daily digest for {target_date or "yesterday"}...')
            result = service.run_daily_digest(target_date)
            self.stdout.write(self.style.SUCCESS(
                f"✓ Digest published for {result['digest_date']} "
                f"({result['articles']} articles, {result['top_story_count']} top stories)"
            ))
            return

        # ── Default: enrich new articles ───────────────────────────────────
        self.stdout.write(f'Running article enrichment (batch={options["batch_size"]})...')
        run = service.run_enrichment()

        if run.status == 'completed':
            self.stdout.write(self.style.SUCCESS(
                f'✓ Enrichment complete | '
                f'Processed: {run.articles_processed} | '
                f'Failed: {run.articles_failed} | '
                f'Cost: ${run.estimated_cost_usd:.4f}'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'⚠ Partial completion | '
                f'Processed: {run.articles_processed} | '
                f'Failed: {run.articles_failed}'
            ))

    def _print_stats(self, service):
        stats = service.get_pipeline_stats()
        e = stats['enrichments']
        c = stats['costs']

        self.stdout.write('\n── Enrichment Pipeline Stats ──────────────────')
        self.stdout.write(f"  Total enrichments : {e['total']}")
        self.stdout.write(f"  Completed         : {e['completed']}")
        self.stdout.write(f"  Failed            : {e['failed']}")
        self.stdout.write(f"  Pending           : {e['pending']}")
        self.stdout.write(f"  Total input tok   : {c['total_input_tokens']:,.0f}")
        self.stdout.write(f"  Total output tok  : {c['total_output_tokens']:,.0f}")
        self.stdout.write(f"  Total cost USD    : ${c['total_cost']:.4f}")
        self.stdout.write('───────────────────────────────────────────────\n')

        # Recent runs
        recent = EnrichmentRun.objects.all()[:5]
        self.stdout.write('Recent runs:')
        for run in recent:
            self.stdout.write(
                f"  [{run.run_type}] {run.status} @ {run.started_at:%Y-%m-%d %H:%M} "
                f"| processed={run.articles_processed} cost=${run.estimated_cost_usd:.4f}"
            )

    def _print_dry_run(self, service):
        articles = service._get_pending_articles()
        self.stdout.write(f'\n── Dry Run: {len(articles)} articles pending enrichment ──')
        for a in articles:
            self.stdout.write(
                f"  [{a.id}] {a.source.name} | {a.word_count}w | {a.title[:70]}"
            )
        self.stdout.write('')
