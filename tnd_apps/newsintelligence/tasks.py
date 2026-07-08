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
        # Batch of 30 covers the tail of the previous scraping cycle that
        # slipped past the hourly enrichment task (e.g. Kawowo runs at :50/:55
        # and its articles are only 15 minutes old when the digest fires at :30).
        # The hourly enrich_new_articles task (batch=50) handles the main backlog.
        if target_date is None:
            service = EnrichmentService(batch_size=30)
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


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name='newsintelligence.tasks.generate_editorial_image',
)
def generate_editorial_image(self, enrichment_id: int) -> dict:
    """
    Generate an AI editorial engraving image for a single ArticleEnrichment.
    Triggered on-demand (admin action or API call) — not scheduled.
    """
    from .models import ArticleEnrichment
    from .editorial_image_service import generate_editorial_image as _generate

    logger.info('[Task] generate_editorial_image | enrichment_id=%d', enrichment_id)
    try:
        enrichment = ArticleEnrichment.objects.select_related('article').get(pk=enrichment_id)
    except ArticleEnrichment.DoesNotExist:
        logger.error('generate_editorial_image: enrichment %d not found', enrichment_id)
        return {'status': 'error', 'reason': 'not_found'}

    try:
        ok = _generate(enrichment)
        return {
            'status': 'ok' if ok else 'skipped',
            'enrichment_id': enrichment_id,
            'image': enrichment.editorial_image.name if ok else None,
        }
    except Exception as exc:
        logger.exception('generate_editorial_image task failed for %d: %s', enrichment_id, exc)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=1,
    name='newsintelligence.tasks.generate_editorial_images_batch',
)
def generate_editorial_images_batch(self, enrichment_ids: list) -> dict:
    """
    Generate editorial images for a list of enrichment IDs in sequence.
    Used by the admin bulk action to avoid blocking the request thread.
    """
    from .editorial_image_service import generate_editorial_image as _generate
    from .models import ArticleEnrichment

    enrichments = ArticleEnrichment.objects.filter(
        pk__in=enrichment_ids
    ).select_related('article')

    done = failed = skipped = 0
    for e in enrichments:
        try:
            ok = _generate(e)
            if ok:
                done += 1
            else:
                skipped += 1
        except Exception as exc:
            logger.error('Batch editorial image failed for enrichment %d: %s', e.pk, exc)
            failed += 1

    logger.info(
        '[Task] generate_editorial_images_batch done | done=%d skipped=%d failed=%d',
        done, skipped, failed,
    )
    return {'done': done, 'skipped': skipped, 'failed': failed}


@shared_task(name='newsintelligence.tasks.build_story_clusters')
def build_story_clusters(days: int = 7):
    from django.core.management import call_command
    call_command('build_story_clusters', f'--days={days}')
    try:
        from tnd_apps.cache_utils import on_clusters_rebuilt
        on_clusters_rebuilt()
    except Exception:
        pass
    return {'status': 'ok', 'days': days}


@shared_task(name='newsintelligence.tasks.send_story_alerts')
def send_story_alerts(limit: int = 20):
    from django.core.management import call_command
    call_command('send_story_alerts', f'--limit={limit}')
    return {'status': 'ok', 'limit': limit}


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    name='newsintelligence.tasks.send_digest_emails',
)
def send_digest_emails(self, target_date_str: str = None, slot: str = 'morning'):
    """
    Send the scheduled email batch for a given slot via Plunk.

    Slots:
      morning  — full daily digest to all subscribers
      evening  — articles roundup to morning_evening subscribers only

    Beat schedule (EAT = UTC+3):
      05:35 UTC → morning  (08:35 EAT)
      15:35 UTC → evening  (18:35 EAT)
    """
    from datetime import datetime
    from .models import DailyDigest
    from .email_service import send_digest_to_all, send_flash_update

    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.error("send_digest_emails: invalid date %r", target_date_str)
            return {'error': f'Invalid date: {target_date_str!r}'}
    else:
        target_date = timezone.localdate()

    logger.info("[Task] send_digest_emails | slot=%s date=%s", slot, target_date)

    try:
        if slot == 'evening':
            result = send_flash_update(slot)
            logger.info(
                "[Task] send_digest_emails [%s] done | sent=%d failed=%d articles=%d",
                slot, result['sent'], result['failed'], result.get('articles_found', 0),
            )
            return {'status': 'ok', 'slot': slot, 'date': str(target_date), **result}

        # Morning: full digest from DailyDigest
        digest = DailyDigest.objects.filter(
            digest_date=target_date, is_published=True
        ).first()

        if not digest:
            logger.warning(
                "send_digest_emails [morning]: no published digest for %s — skipping", target_date
            )
            return {'status': 'skipped', 'reason': 'no published digest', 'date': str(target_date)}

        result = send_digest_to_all(digest)
        logger.info(
            "[Task] send_digest_emails [morning] done | date=%s sent=%d failed=%d",
            target_date, result['sent'], result['failed'],
        )
        return {'status': 'ok', 'slot': 'morning', 'date': str(target_date), **result}

    except Exception as exc:
        logger.exception("send_digest_emails [%s] failed for %s: %s", slot, target_date, exc)
        raise self.retry(exc=exc)
