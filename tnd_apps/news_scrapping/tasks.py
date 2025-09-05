from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from .scraper import TNDNewsDjangoScraper
from .dokolo_scraper import DokoloPostDjangoScraper
from .models import ScrapingRun, ScrapingLog
from .dm_scrapper import MonitorNewsDjangoScraper
import traceback

logger = get_task_logger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def scrape_dm_uganda(self, get_full_content=False, max_articles=None, source_name="Daily Monitor"):
    try:
        logger.info(f"Starting DM News scraping task - Task ID: {self.request.id}")

        scraper = MonitorNewsDjangoScraper(source_name=source_name)

        # Update the scraping run with task ID
        latest_run = ScrapingRun.objects.filter(
            source=scraper.source,
            status='started'
        ).order_by('-started_at').first()

        if latest_run:
            latest_run.task_id = self.request.id
            latest_run.save()

        result = scraper.scrape_and_save(
            get_full_content=get_full_content,
            max_articles=max_articles
        )
        logger.info(f"DM News scraping completed successfully: {result}")
        return result
    except Exception as exc:
        logger.error(f"DM News scraping failed: {str(exc)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

        # Update run status if exists
        latest_run = ScrapingRun.objects.filter(
            task_id=self.request.id
        ).first()

        if latest_run:
            latest_run.status = 'failed'
            latest_run.error_message = str(exc)
            latest_run.save()

        # Retry logic
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying task in {self.default_retry_delay} seconds...")
            raise self.retry(countdown=self.default_retry_delay, exc=exc)

        raise exc


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def scrape_tnd_news(self, get_full_content=True, max_articles=None, source_name="TND News Uganda"):
    """
    Celery task to scrape TND News articles
    """
    try:
        logger.info(f"Starting TND News scraping task - Task ID: {self.request.id}")

        scraper = TNDNewsDjangoScraper(source_name=source_name)

        # Update the scraping run with task ID
        latest_run = ScrapingRun.objects.filter(
            source=scraper.source,
            status='started'
        ).order_by('-started_at').first()

        if latest_run:
            latest_run.task_id = self.request.id
            latest_run.save()

        result = scraper.scrape_and_save(
            get_full_content=get_full_content,
            max_articles=max_articles
        )

        logger.info(f"TND News scraping completed successfully: {result}")
        return result

    except Exception as exc:
        logger.error(f"TND News scraping failed: {str(exc)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

        # Update run status if exists
        latest_run = ScrapingRun.objects.filter(
            task_id=self.request.id
        ).first()

        if latest_run:
            latest_run.status = 'failed'
            latest_run.error_message = str(exc)
            latest_run.save()

        # Retry logic
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying task in {self.default_retry_delay} seconds...")
            raise self.retry(countdown=self.default_retry_delay, exc=exc)

        raise exc


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def scrape_dokolo_post(self, get_full_content=True, max_articles=None, source_name="Dokolo Post"):
    """
    Celery task to scrape Dokolo Post articles
    """
    try:
        logger.info(f"Starting Dokolo Post scraping task - Task ID: {self.request.id}")

        scraper = DokoloPostDjangoScraper(source_name=source_name)

        # Update the scraping run with task ID
        latest_run = ScrapingRun.objects.filter(
            source=scraper.source,
            status='started'
        ).order_by('-started_at').first()

        if latest_run:
            latest_run.task_id = self.request.id
            latest_run.save()

        result = scraper.scrape_and_save(
            get_full_content=get_full_content,
            max_articles=max_articles
        )

        logger.info(f"Dokolo Post scraping completed successfully: {result}")
        return result

    except Exception as exc:
        logger.error(f"Dokolo Post scraping failed: {str(exc)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

        # Update run status if exists
        latest_run = ScrapingRun.objects.filter(
            task_id=self.request.id
        ).first()

        if latest_run:
            latest_run.status = 'failed'
            latest_run.error_message = str(exc)
            latest_run.save()

        # Retry logic
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying task in {self.default_retry_delay} seconds...")
            raise self.retry(countdown=self.default_retry_delay, exc=exc)

        raise exc



@shared_task
def cleanup_old_scraping_logs(days_to_keep=30):
    """
    Clean up old scraping logs to prevent database bloat
    """
    from django.utils import timezone
    from datetime import timedelta

    cutoff_date = timezone.now() - timedelta(days=days_to_keep)

    deleted_logs = ScrapingLog.objects.filter(
        timestamp__lt=cutoff_date
    ).delete()

    deleted_runs = ScrapingRun.objects.filter(
        started_at__lt=cutoff_date,
        status__in=['completed', 'failed']
    ).delete()

    logger.info(f"Cleaned up {deleted_logs[0]} old logs and {deleted_runs[0]} old runs")

    return {
        'logs_deleted': deleted_logs[0],
        'runs_deleted': deleted_runs[0]
    }


@shared_task
def health_check_task():
    """
    Simple health check task to verify Celery is working
    """
    logger.info("Health check task executed successfully")
    return {"status": "healthy"}