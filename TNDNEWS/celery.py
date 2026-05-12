import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'TNDNEWS.settings')

celery_app = Celery('TNDNEWS')
celery_app.config_from_object('django.conf:settings', namespace='CELERY')
celery_app.autodiscover_tasks()

celery_app.conf.timezone = 'UTC'

celery_app.conf.beat_schedule = {
    'send-scheduled-notifications': {
        'task': 'tnd_apps.news_scrapping.tasks.send_scheduled_notifications',
        'schedule': crontab(minute='*/15'),  # Every 15 minutes
    },
    'process-queued-videos': {
        'task': 'tnd_apps.tndvideo.tasks.process_queued_videos',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    'cleanup-old-processing-tasks': {
        'task': 'tnd_apps.tndvideo.tasks.cleanup_old_processing_tasks',
        'schedule': crontab(hour='*/1'),  # Every hour
    },
    'cleanup-failed-uploads': {
        'task': 'tnd_apps.tndvideo.tasks.cleanup_failed_uploads_task',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2 AM
    },
    'cleanup-failed-tasks': {
        'task': 'tnd_apps.news_scrapping.tasks.health_check_task',
        'schedule': 300.0,  # Run every 5 minutes
    },
    'cleanup-old-notifications': {
        'task': 'tnd_apps.news_scrapping.tasks.cleanup_old_notifications',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2 AM
    },
'enrich-articles-hourly': {
        'task': 'newsintelligence.tasks.enrich_new_articles',
        'schedule': crontab(minute=15),
    },
    'retry-failed-enrichments': {
        'task': 'newsintelligence.tasks.retry_failed_enrichments',
        'schedule': crontab(minute=0, hour='*/6'),
    },
    'generate-daily-digest': {
        'task': 'newsintelligence.tasks.generate_daily_digest',
        'schedule': crontab(minute=0, hour=3),
    },
    'build-story-clusters-hourly': {
        'task': 'newsintelligence.tasks.build_story_clusters',
        'schedule': crontab(minute=45),
    },
    'send-story-alerts': {
        'task': 'newsintelligence.tasks.send_story_alerts',
        'schedule': crontab(minute='*/10'),
    },
    'scrape-daily-monitor-news': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_dm_uganda',
        'schedule': crontab(minute=10, hour='*/3'),
        'kwargs': {'get_full_content': True, 'max_articles': 25},
    },
    'scrape-chimpreports-news': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_chimpreports_news',
        'schedule': crontab(minute=20, hour='*/3'),
        'kwargs': {'get_full_content': True, 'max_articles': 25, 'max_pages': 2},
    },
    'scrape-ubc-news': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_ubc_news',
        'schedule': crontab(minute=35, hour='*/3'),
        'kwargs': {'get_full_content': True, 'max_articles': 25, 'max_pages': 2},
    },
  'scrape-nilepost-news': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_nilepost_section',
        'schedule': crontab(minute=0, hour='*/3'),   # :00 — 00:00, 03:00, 06:00 …
        'kwargs': {'section': 'news', 'get_full_content': True, 'max_pages': 2},
    },
    'scrape-nilepost-opinions': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_nilepost_section',
        'schedule': crontab(minute=15, hour='*/3'),  # :15
        'kwargs': {'section': 'opinions', 'get_full_content': True, 'max_pages': 2},
    },
    'scrape-nilepost-politics': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_nilepost_section',
        'schedule': crontab(minute=30, hour='*/3'),  # :30
        'kwargs': {'section': 'politics', 'get_full_content': True, 'max_pages': 2},
    },
    'scrape-nilepost-security': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_nilepost_section',
        'schedule': crontab(minute=45, hour='*/3'),  # :45
        'kwargs': {'section': 'security', 'get_full_content': True, 'max_pages': 2},
    },
}
