"""
Celery tasks for the enrichment pipeline.

Add to your CELERY_BEAT_SCHEDULE in settings.py:

    from celery.schedules import crontab

    CELERY_BEAT_SCHEDULE = {
        # Enrich new articles every hour
        'enrich-articles-hourly': {
            'task': 'newsintelligence.tasks.enrich_new_articles',
            'schedule': crontab(minute=15),  # :15 past every hour
        },
        # Retry failed enrichments every 6 hours
        'retry-failed-enrichments': {
            'task': 'newsintelligence.tasks.retry_failed_enrichments',
            'schedule': crontab(minute=0, hour='*/6'),
        },
        # Refresh today's digest through the day
        'generate-daily-digest': {
            'task': 'newsintelligence.tasks.generate_daily_digest',
            'schedule': crontab(minute=0, hour='3,9,15'),
        },
    }
"""

import logging
from datetime import date

from celery import shared_task
from django.utils import timezone

from .services import EnrichmentService

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='newsintelligence.tasks.enrich_new_articles',
)
def enrich_new_articles(self, batch_size: int = 50):
    """
    Hourly task: enrich all articles with has_full_content=True
    that haven't been processed yet.
    """
    logger.info("[Task] enrich_new_articles | batch_size=%d", batch_size)
    try:
        service = EnrichmentService(batch_size=batch_size)
        run = service.run_enrichment()
        return {
            'run_id':    run.id,
            'processed': run.articles_processed,
            'failed':    run.articles_failed,
            'cost_usd':  float(run.estimated_cost_usd),
        }
    except Exception as exc:
        logger.exception("enrich_new_articles failed: %s", exc)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=2,
    name='newsintelligence.tasks.retry_failed_enrichments',
)
def retry_failed_enrichments(self):
    """
    Retry articles that failed on the previous enrichment run.
    """
    logger.info("[Task] retry_failed_enrichments")
    try:
        service = EnrichmentService()
        run = service.run_retry_failed()
        return {
            'run_id':    run.id,
            'processed': run.articles_processed,
            'failed':    run.articles_failed,
        }
    except Exception as exc:
        logger.exception("retry_failed_enrichments failed: %s", exc)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=2,
    name='newsintelligence.tasks.generate_daily_digest',
)
def generate_daily_digest(
    self,
    target_date_str: str = None,
    force_refresh: bool = True,
    slot: str = '',
):
    """
    Generate today's intelligence digest (or backfill a past date).

    Runs 4× a day at 08:30, 12:30, 18:30, 21:30 EAT.  Each run:
      1. Top-up enrichment — small batch (10) for any articles scraped in the
         last 2 hours that the hourly task may not have reached yet.  The
         hourly enrich_new_articles task (batch=50) handles the main backlog;
         we only catch the gap between that last run and now.
      2. Regenerate today's digest from all completed enrichments.

    Pass target_date_str='YYYY-MM-DD' to backfill a past date (skips top-up).
    """
    target_date = None
    if target_date_str:
        from datetime import datetime
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.error("Invalid date format %r — expected YYYY-MM-DD", target_date_str)
            return {'error': f"Invalid date format: {target_date_str!r}. Expected YYYY-MM-DD."}

    label = f"[slot={slot}]" if slot else ''
    logger.info("[Task] generate_daily_digest %s | date=%s", label, target_date or timezone.localdate())

    try:
        # Top-up enrichment — only for scheduled (non-backfill) runs.
        # Small batch so the digest task stays fast; the hourly enrichment
        # task covers the full backlog independently.
        if target_date is None:
            service = EnrichmentService(batch_size=10)
            enrichment_run = service.run_enrichment()
            logger.info(
                "[Task] pre-digest top-up %s | processed=%d failed=%d",
                label,
                enrichment_run.articles_processed,
                enrichment_run.articles_failed,
            )
        else:
            service = EnrichmentService()

        refresh_existing = force_refresh and target_date is None
        result = service.run_daily_digest(target_date, force_refresh=refresh_existing)
        result['slot'] = slot
        return result

    except Exception as exc:
        logger.exception("generate_daily_digest %s failed: %s", label, exc)
        raise self.retry(exc=exc)


@shared_task(name='newsintelligence.tasks.build_story_clusters')
def build_story_clusters(days: int = 7):
    from django.core.management import call_command
    call_command('build_story_clusters', f'--days={days}')
    return {'status': 'ok', 'days': days}


@shared_task(name='newsintelligence.tasks.send_story_alerts')
def send_story_alerts(limit: int = 20):
    from django.core.management import call_command
    call_command('send_story_alerts', f'--limit={limit}')
    return {'status': 'ok', 'limit': limit}
