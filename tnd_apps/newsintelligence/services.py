"""
EnrichmentService
─────────────────
Orchestrates the full pipeline:
  1. Fetch unenriched articles (has_full_content=True)
  2. Run ArticleAnalysisAgent on each
  3. Run EntityExtractionAgent on each
  4. Optionally generate DailyDigest

Designed to be called from:
  - Celery tasks (beat schedule)
  - Django management commands
  - Airflow PythonOperator
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from .agents import ArticleAnalysisAgent, DailyDigestAgent, EntityExtractionAgent
from .openai_client import calculate_cost, ENRICHMENT_MODEL
from .models import ArticleEnrichment, EnrichmentRun

logger = logging.getLogger(__name__)


class EnrichmentService:

    def __init__(self, batch_size: int = 50, max_retries: int = 2):
        self.batch_size  = batch_size
        self.max_retries = max_retries

        self.analysis_agent   = ArticleAnalysisAgent()
        self.entity_agent     = EntityExtractionAgent()
        self.digest_agent     = DailyDigestAgent()

    # ── Public API ────────────────────────────────────────────────────────────

    def run_enrichment(self) -> EnrichmentRun:
        """
        Fetch and enrich all pending articles.
        Returns a completed EnrichmentRun with stats.
        """
        run = EnrichmentRun.objects.create(run_type='enrichment', status='started')
        logger.info("=== EnrichmentRun #%d started ===", run.id)

        articles = self._get_pending_articles()
        run.articles_found = len(articles)
        run.save(update_fields=['articles_found'])

        if not articles:
            logger.info("No pending articles — nothing to do.")
            run.status = 'completed'
            run.completed_at = timezone.now()
            run.save()
            return run

        total_input  = 0
        total_output = 0

        for article in articles:
            try:
                enrichment = self.analysis_agent.process(article)
                self.entity_agent.process(enrichment)

                total_input  += enrichment.input_tokens_used
                total_output += enrichment.output_tokens_used
                run.articles_processed += 1

            except Exception as e:
                run.articles_failed += 1
                logger.error("Failed on article %d: %s", article.id, e)

        run.total_input_tokens  = total_input
        run.total_output_tokens = total_output
        run.estimated_cost_usd  = Decimal(str(
            calculate_cost(ENRICHMENT_MODEL, total_input, total_output)
        ))
        run.status       = 'completed' if run.articles_failed == 0 else 'partial'
        run.completed_at = timezone.now()
        run.save()

        logger.info(
            "=== EnrichmentRun #%d done | processed=%d failed=%d cost=$%.4f ===",
            run.id, run.articles_processed, run.articles_failed,
            run.estimated_cost_usd,
        )
        return run

    def run_retry_failed(self) -> EnrichmentRun:
        """Retry all articles whose enrichment previously failed."""
        run = EnrichmentRun.objects.create(run_type='retry', status='started')

        failed = list(
            ArticleEnrichment.objects.filter(
                status='failed',
                retry_count__lt=self.max_retries,
            ).select_related('article')[:self.batch_size]
        )
        run.articles_found = len(failed)

        for enrichment in failed:
            # Reset to pending so agent reprocesses it
            enrichment.status = 'pending'
            enrichment.save(update_fields=['status'])
            try:
                self.analysis_agent.process(enrichment.article)
                self.entity_agent.process(enrichment)
                run.articles_processed += 1
            except Exception:
                run.articles_failed += 1

        run.status       = 'completed'
        run.completed_at = timezone.now()
        run.save()
        return run

    def run_daily_digest(self, target_date: Optional[date] = None) -> dict:
        """
        Generate the daily digest for the given date (default: yesterday).
        Returns a summary dict.
        """
        run = EnrichmentRun.objects.create(run_type='daily_digest', status='started')

        try:
            digest = self.digest_agent.generate(target_date)
            run.articles_analyzed  = digest.articles_analyzed
            run.total_input_tokens  = digest.input_tokens_used
            run.total_output_tokens = digest.output_tokens_used
            run.status       = 'completed'
            run.completed_at = timezone.now()
            run.save()

            return {
                'digest_date':    str(digest.digest_date),
                'articles':       digest.articles_analyzed,
                'top_story_count': len(digest.top_stories),
                'is_published':   digest.is_published,
            }

        except Exception as e:
            run.status        = 'failed'
            run.error_message = str(e)
            run.completed_at  = timezone.now()
            run.save()
            raise

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_pending_articles(self):
        """
        Fetch articles that:
        - have full content
        - have NOT been enriched yet (no ArticleEnrichment record,
          or enrichment is still pending)
        Ordered by most recent first. Capped at batch_size.
        """
        from django.apps import apps
        Article = apps.get_model('news_scrapping', 'Article')  # <-- replace 'news' with your app name

        # IDs already handled (completed or currently processing)
        handled_ids = ArticleEnrichment.objects.filter(
            status__in=['completed', 'processing']
        ).values_list('article_id', flat=True)

        return list(
            Article.objects.filter(
                has_full_content=True,
            ).exclude(
                id__in=handled_ids,
            ).select_related('source', 'category')
            .order_by('-scraped_at')[:self.batch_size]
        )

    def get_pipeline_stats(self) -> dict:
        """Quick stats for monitoring dashboards."""
        from django.db.models import Count, Sum, Avg

        enrichment_stats = ArticleEnrichment.objects.aggregate(
            total=Count('id'),
            completed=Count('id', filter=__import__('django.db.models', fromlist=['Q']).Q(status='completed')),
            failed=Count('id', filter=__import__('django.db.models', fromlist=['Q']).Q(status='failed')),
            pending=Count('id', filter=__import__('django.db.models', fromlist=['Q']).Q(status='pending')),
        )

        cost_stats = EnrichmentRun.objects.aggregate(
            total_cost=Sum('estimated_cost_usd'),
            total_input_tokens=Sum('total_input_tokens'),
            total_output_tokens=Sum('total_output_tokens'),
        )

        return {
            'enrichments': enrichment_stats,
            'costs': {k: float(v or 0) for k, v in cost_stats.items()},
        }
