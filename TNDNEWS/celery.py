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
    'update-source-favicons': {
        'task': 'tnd_apps.news_scrapping.tasks.update_source_favicons',
        'schedule': crontab(hour=1, minute=30),  # Daily at 1:30 AM UTC
        'kwargs': {'refresh_all': False, 'include_inactive': False},
    },
'enrich-articles-hourly': {
        'task': 'newsintelligence.tasks.enrich_new_articles',
        'schedule': crontab(minute=15),
    },
    'retry-failed-enrichments': {
        'task': 'newsintelligence.tasks.retry_failed_enrichments',
        'schedule': crontab(minute=0, hour='*/6'),
    },
    # Digest runs 4× a day, always 30 min after the :15 enrichment cycle so
    # freshly scraped articles are already processed before synthesis begins.
    # Times in EAT (UTC+3):
    #   05:30 UTC = 08:30 EAT — morning briefing (overnight + early papers)
    #   09:30 UTC = 12:30 EAT — midday update  (morning news cycle)
    #   15:30 UTC = 18:30 EAT — evening wrap-up (afternoon news cycle)
    #   18:30 UTC = 21:30 EAT — night update    (prime-time TV stories online)
    'generate-daily-digest-morning': {
        'task': 'newsintelligence.tasks.generate_daily_digest',
        'schedule': crontab(minute=30, hour=5),
        'kwargs': {'slot': 'morning'},
    },
    'generate-daily-digest-midday': {
        'task': 'newsintelligence.tasks.generate_daily_digest',
        'schedule': crontab(minute=30, hour=9),
        'kwargs': {'slot': 'midday'},
    },
    'generate-daily-digest-evening': {
        'task': 'newsintelligence.tasks.generate_daily_digest',
        'schedule': crontab(minute=30, hour=15),
        'kwargs': {'slot': 'evening'},
    },
    'generate-daily-digest-night': {
        'task': 'newsintelligence.tasks.generate_daily_digest',
        'schedule': crontab(minute=30, hour=18),
        'kwargs': {'slot': 'night'},
    },
    # Two email sends per day (EAT = UTC+3):
    #   05:35 UTC → 08:35 EAT  morning digest  (all subscribers)
    #   15:35 UTC → 18:35 EAT  evening roundup (morning_evening subscribers)
    'send-digest-emails-morning': {
        'task': 'newsintelligence.tasks.send_digest_emails',
        'schedule': crontab(minute=35, hour=5),   # 08:35 EAT
        'kwargs': {'slot': 'morning'},
    },
    'send-digest-emails-evening': {
        'task': 'newsintelligence.tasks.send_digest_emails',
        'schedule': crontab(minute=35, hour=15),  # 18:35 EAT
        'kwargs': {'slot': 'evening'},
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
    'scrape-observer-news': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_observer_section',
        'schedule': crontab(minute=5, hour='*/3'),   # :05 — 00:05, 03:05, 06:05 …
        'kwargs': {'section': 'news', 'get_full_content': True, 'max_articles': 25, 'max_pages': 2},
    },
    'scrape-observer-business': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_observer_section',
        'schedule': crontab(minute=25, hour='*/3'),  # :25
        'kwargs': {'section': 'business', 'get_full_content': True, 'max_articles': 20, 'max_pages': 2},
    },
    'scrape-kawowo-home': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_kawowo_section',
        'schedule': crontab(minute=50, hour='*/3'),  # :50 — 00:50, 03:50, 06:50 …
        'kwargs': {'section': 'home', 'get_full_content': True, 'max_articles': 25, 'max_pages': 2},
    },
    'scrape-kawowo-football': {
        'task': 'tnd_apps.news_scrapping.tasks.scrape_kawowo_section',
        'schedule': crontab(minute=55, hour='*/3'),  # :55
        'kwargs': {'section': 'football', 'get_full_content': True, 'max_articles': 25, 'max_pages': 2},
    },
}
