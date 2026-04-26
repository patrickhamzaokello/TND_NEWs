import logging

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from tnd_apps.news_scrapping.models import PushToken
from tnd_apps.newsintelligence.models import StoryAlert

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send pending high-signal story alerts to active push-token users.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        alerts = StoryAlert.objects.filter(status='pending').select_related(
            'cluster', 'article', 'article__source'
        ).order_by('-importance_score', 'created_at')[:options['limit']]

        sent = 0
        for alert in alerts:
            if options['dry_run']:
                self.stdout.write(f"DRY RUN: would send alert {alert.id}: {alert.title}")
                continue
            if self._send_alert(alert):
                alert.status = 'sent'
                alert.sent_at = timezone.now()
                alert.save(update_fields=['status', 'sent_at'])
                sent += 1

        self.stdout.write(self.style.SUCCESS(f'Sent {sent} story alerts.'))

    def _send_alert(self, alert):
        User = get_user_model()
        user_ids = User.objects.filter(push_tokens__is_active=True).values_list('id', flat=True).distinct()
        tokens = PushToken.objects.filter(user_id__in=user_ids, is_active=True)
        messages = [
            {
                'token': token.token,
                'title': alert.title,
                'body': alert.reason[:180],
                'metadata': {
                    'notificationType': 'story_alert',
                    'clusterId': str(alert.cluster_id),
                    'articleId': str(alert.article_id),
                    'importanceScore': alert.importance_score,
                    'source': 'news_intelligence',
                },
            }
            for token in tokens
        ]

        if not messages:
            logger.info("No active push tokens for story alert %s", alert.id)
            return False

        response = requests.post(
            settings.NOTIFICATION_SERVICE_URL,
            json={'messages': messages},
            headers={'Content-Type': 'application/json'},
            timeout=30,
        )
        if response.status_code != 200:
            logger.error("Story alert send failed: status=%s body=%s", response.status_code, response.text[:500])
            return False
        return True
