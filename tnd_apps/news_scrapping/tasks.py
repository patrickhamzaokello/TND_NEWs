from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings

from .nilepost_scrapper import NilePostScraper
from .scraper import TNDNewsDjangoScraper
from .kampalatimesscrapper import KampalaTimesDjangoScraper
from .dokolo_scraper import DokoloPostDjangoScraper
from .models import ScrapingRun, ScrapingLog,ScheduledNotification
from .dm_scrapper import MonitorNewsDjangoScraper
from .exclusive_bizz_scrapper import ExclusiveCoUgScraper
import traceback
from django.utils import timezone
from django.core.management import call_command

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
def scrape_kampalatimes_news(self, get_full_content=True, max_articles=None, source_name="Kampala Edge Times"):
    """
    Celery task to scrape Kampala Edge Times
    """
    try:
        logger.info(f"Starting Kampala Edge Times scraping task - Task ID: {self.request.id}")

        scraper = KampalaTimesDjangoScraper(source_name=source_name)

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

        logger.info(f"Kampala Edge Times scraping completed successfully: {result}")
        return result

    except Exception as exc:
        logger.error(f"Kampala Edge Times scraping failed: {str(exc)}")
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
def scrape_exlusive_bizz(self, get_full_content=True, max_articles=None, source_name="Exclusive Bizz"):
    try:
        logger.info(f"Starting Exclusive Bizz News scraping task - Task ID: {self.request.id}")

        scraper = ExclusiveCoUgScraper(source_name=source_name)

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
        logger.error(f"Exclusive Bizz News scraping failed: {str(exc)}")
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
def send_scheduled_notifications():
    """Celery task to send scheduled notifications"""
    from django.core.management import call_command
    call_command('send_scheduled_notifications')


@shared_task
def send_breaking_news_immediately(article_id=None, breaking_news_id=None):
    """Celery task for immediate breaking news delivery"""
    from django.core.management import call_command

    if article_id:
        call_command('send_breaking_news', f'--article-id={article_id}')
    elif breaking_news_id:
        call_command('send_breaking_news', f'--breaking-news-id={breaking_news_id}')
    else:
        call_command('send_breaking_news')

@shared_task
def process_new_article_for_breaking_news(article_id):
    """Check if new article should be breaking news"""
    from .models import Article, BreakingNews
    
    try:
        article = Article.objects.get(id=article_id)
        
        # Your breaking news detection logic here
        if should_be_breaking_news(article):
            breaking_news = BreakingNews.objects.create(
                article=article,
                priority='high'
            )
            # Send immediately
            send_breaking_news_immediately.delay(breaking_news_id=breaking_news.id)
            
    except Article.DoesNotExist:
        pass

def should_be_breaking_news(article):
    """Determine if article should be treated as breaking news"""
    breaking_keywords = ['breaking', 'urgent', 'alert', 'crisis', 'disaster']
    
    title_lower = article.title.lower()
    return any(keyword in title_lower for keyword in breaking_keywords)


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
def cleanup_old_notifications():
    """Periodic task to clean up old notifications"""
    from django.core.management import call_command
    call_command('cleanup_notification_history', '--days=30')

@shared_task
def health_check_task():
    """
    Simple health check task to verify Celery is working
    """
    logger.info("Health check task executed successfully")
    return {"status": "healthy"}


NILEPOST_SECTIONS: dict[str, str] = {
    "news":     "https://nilepost.co.ug/news",
    "opinions": "https://nilepost.co.ug/opinions",
    "politics": "https://nilepost.co.ug/politics",
    "security": "https://nilepost.co.ug/security",
}

DEFAULT_SOURCE_NAME = "NilePost"


# ── Helper ─────────────────────────────────────────────────────────────────

def _attach_task_id(scraper: NilePostScraper, task_id: str) -> None:
    """
    Find the most recent 'started' ScrapingRun for this source and
    stamp it with the Celery task ID so it can be tracked externally.
    """
    run = (
        ScrapingRun.objects.filter(source=scraper.source, status="started")
        .order_by("-started_at")
        .first()
    )
    if run:
        run.task_id = task_id
        run.save(update_fields=["task_id"])


def _mark_run_failed(task_id: str, error_message: str) -> None:
    """
    If a ScrapingRun was created for this task, mark it as failed.
    """
    run = ScrapingRun.objects.filter(task_id=task_id).first()
    if run:
        run.status = "failed"
        run.error_message = error_message
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error_message", "completed_at"])


# ── Per-section task ───────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,          # 5 minutes between retries
    # name="news_scrapping.tasks.scrape_nilepost_section",
    acks_late=True,                   # only ack after successful execution
    reject_on_worker_lost=True,       # re-queue if worker dies mid-task
)
def scrape_nilepost_section(
    self,
    section: str = "news",
    get_full_content: bool = True,
    max_articles: int | None = None,
    max_pages: int = 1,
    start_page: int = 1,
    source_name: str = DEFAULT_SOURCE_NAME,
):
    """
    Scrape a single NilePost section and persist articles to the database.

    Args:
        section:          One of the keys in NILEPOST_SECTIONS
                          ('news', 'opinions', 'politics', 'security').
        get_full_content: Visit each article's detail page for full body text.
        max_articles:     Hard cap on articles processed (None = unlimited).
        max_pages:        How many listing pages to paginate through.
        start_page:       Listing page to start from (1-indexed).
        source_name:      NewsSource.name to look up / create.

    Returns:
        Result dict from NilePostScraper.scrape_and_save().
    """
    section = section.lower()

    if section not in NILEPOST_SECTIONS:
        valid = ", ".join(NILEPOST_SECTIONS)
        raise ValueError(
            f"Unknown section '{section}'. Valid options: {valid}"
        )

    listing_url = NILEPOST_SECTIONS[section]
    task_id = self.request.id

    logger.info(
        "Starting NilePost scrape | section=%s | url=%s | "
        "full_content=%s | max_articles=%s | max_pages=%s | task_id=%s",
        section, listing_url, get_full_content, max_articles, max_pages, task_id,
    )

    try:
        scraper = NilePostScraper(source_name=source_name, headless=True)

        # The scraper creates a ScrapingRun internally when scrape_and_save()
        # is called. We pass the task ID immediately after so it appears on the run.
        # Because scrape_and_save starts the run, we hook in via a thin wrapper
        # that stamps the ID after the run object is created.

        # Monkey-patch: wrap the original scrape_and_save to capture the run
        _original = scraper.scrape_and_save

        def _patched_scrape_and_save(**kwargs):
            # Let the scraper create its run, then stamp the task_id
            import threading

            def _stamp():
                import time
                time.sleep(0.5)  # brief wait for the run row to be committed
                _attach_task_id(scraper, task_id)

            threading.Thread(target=_stamp, daemon=True).start()
            return _original(**kwargs)

        result = _patched_scrape_and_save(
            get_full_content=get_full_content,
            max_articles=max_articles,
            start_page=start_page,
            max_pages=max_pages,
            news_url=listing_url,
        )

        logger.info(
            "NilePost scrape complete | section=%s | added=%s | updated=%s | "
            "skipped=%s | errors=%s | duration=%ss",
            section,
            result.get("articles_added", 0),
            result.get("articles_updated", 0),
            result.get("articles_skipped", 0),
            result.get("errors", 0),
            result.get("duration", "?"),
        )
        return result

    except Exception as exc:
        logger.error(
            "NilePost scrape FAILED | section=%s | task_id=%s | error=%s\n%s",
            section, task_id, exc, traceback.format_exc(),
        )

        _mark_run_failed(task_id, str(exc))

        if self.request.retries < self.max_retries:
            logger.info(
                "Retrying section=%s in %ss (attempt %s/%s)…",
                section,
                self.default_retry_delay,
                self.request.retries + 1,
                self.max_retries,
            )
            raise self.retry(countdown=self.default_retry_delay, exc=exc)

        # All retries exhausted — re-raise so Celery marks the task FAILURE
        raise exc