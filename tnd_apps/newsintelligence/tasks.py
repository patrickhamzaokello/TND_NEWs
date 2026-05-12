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
def generate_daily_digest(self, target_date_str: str = None, force_refresh: bool = True):
    """
    Daily task: enrich pending full-content articles, then generate today's
    intelligence digest unless a date is supplied for backfill.
    Scheduled current-day runs refresh the published digest as new stories arrive.
    Optionally pass target_date_str='2026-02-14' to backfill.
    """
    target_date = None
    if target_date_str:
        from datetime import datetime
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.error("Invalid date format %r — expected YYYY-MM-DD", target_date_str)
            return {'error': f"Invalid date format: {target_date_str!r}. Expected YYYY-MM-DD."}

    logger.info("[Task] generate_daily_digest | date=%s", target_date or timezone.localdate())
    try:
        service = EnrichmentService(batch_size=50)
        enrichment_run = service.run_enrichment()
        logger.info(
            "[Task] pre-digest enrichment complete | processed=%d failed=%d",
            enrichment_run.articles_processed,
            enrichment_run.articles_failed,
        )
        refresh_existing = force_refresh and target_date is None
        result = service.run_daily_digest(target_date, force_refresh=refresh_existing)
        return result
    except Exception as exc:
        logger.exception("generate_daily_digest failed: %s", exc)
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
