import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'TNDNEWS.settings')

celery_app = Celery('TNDNEWS')
celery_app.config_from_object('django.conf:settings', namespace='CELERY')
celery_app.autodiscover_tasks()

celery_app.conf.timezone = 'UTC'

# Celery beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    'cleanup-failed-tasks': {
        'task': 'news_scrapping.tasks.check_for_news',
        'schedule': 300.0,  # Run every 5 minutes
    },
}