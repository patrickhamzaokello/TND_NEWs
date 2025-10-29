from django.core.management.base import BaseCommand
from django.utils import timezone
from tnd_apps.news_scrapping.models import (
    BreakingNews, Article, PushToken, UserProfile,
    UserNotification, ArticleNotificationHistory
)
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send breaking news notifications'

    def add_arguments(self, parser):
        parser.add_argument('--article-id', type=int, help='Specific article ID')
        parser.add_argument('--breaking-news-id', type=int, help='Specific breaking news ID')
        parser.add_argument('--dry-run', action='store_true', help='Simulate without sending')

    def handle(self, *args, **options):
        if options['article_id']:
            self.send_article_as_breaking_news(options['article_id'], options['dry_run'])
        elif options['breaking_news_id']:
            self.send_specific_breaking_news(options['breaking_news_id'], options['dry_run'])
        else:
            self.send_all_breaking_news(options['dry_run'])

    def send_article_as_breaking_news(self, article_id, dry_run=False):
        """Send a specific article as breaking news"""
        try:
            article = Article.objects.get(id=article_id)
            breaking_news, created = BreakingNews.objects.get_or_create(
                article=article,
                defaults={'priority': 'high'}
            )

            if not breaking_news.is_sent:
                self.send_breaking_news_notification(breaking_news, dry_run)
            else:
                self.stdout.write(self.style.WARNING(
                    f"Breaking news for article {article_id} was already sent"
                ))
        except Article.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Article {article_id} not found"))

    def send_specific_breaking_news(self, breaking_news_id, dry_run=False):
        """Send a specific breaking news entry"""
        try:
            breaking_news = BreakingNews.objects.get(id=breaking_news_id)
            if not breaking_news.is_sent:
                self.send_breaking_news_notification(breaking_news, dry_run)
            else:
                self.stdout.write(self.style.WARNING(
                    f"Breaking news {breaking_news_id} was already sent"
                ))
        except BreakingNews.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Breaking news {breaking_news_id} not found"))

    def send_all_breaking_news(self, dry_run=False):
        """Send all unsent breaking news"""
        unsent_news = BreakingNews.objects.filter(is_sent=False)
        self.stdout.write(f"Found {unsent_news.count()} unsent breaking news items")

        for breaking_news in unsent_news:
            self.send_breaking_news_notification(breaking_news, dry_run)

    def get_users_to_notify(self, breaking_news):
        """Get users who should receive this breaking news, excluding those who already got it"""
        from django.contrib.auth import get_user_model
        User = get_user_model()

        article = breaking_news.article

        # Check who already received this article (within last 24 hours)
        cutoff = timezone.now() - timedelta(hours=24)
        already_notified_users = ArticleNotificationHistory.objects.filter(
            article=article,
            sent_at__gte=cutoff
        ).values_list('user_id', flat=True)

        logger.info(f"Excluding {len(already_notified_users)} users who already received article {article.id}")

        base_query = User.objects.filter(
            push_tokens__is_active=True
        ).exclude(
            id__in=already_notified_users  # Exclude users who already got this article
        ).distinct()

        # Apply targeting filters
        if breaking_news.target_categories.exists():
            base_query = base_query.filter(
                user_profiles__preferred_categories__in=breaking_news.target_categories.all()
            )
        elif article.category:
            base_query = base_query.filter(
                user_profiles__preferred_categories=article.category
            )

        if breaking_news.target_sources.exists():
            base_query = base_query.filter(
                user_profiles__followed_sources__in=breaking_news.target_sources.all()
            )
        elif article.source:
            base_query = base_query.filter(
                user_profiles__followed_sources=article.source
            )

        return base_query

    def send_breaking_news_notification(self, breaking_news, dry_run=False):
        """Send breaking news notification to relevant users"""

        article = breaking_news.article
        users_to_notify = self.get_users_to_notify(breaking_news)

        self.stdout.write(f"Preparing to send breaking news to {users_to_notify.count()} users")

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"DRY RUN: Would send '{article.title}' to {users_to_notify.count()} users"
            ))
            return

        all_messages = []
        user_list = []

        for user in users_to_notify:
            user_messages = self.prepare_user_breaking_news(user, article, breaking_news.priority)
            if user_messages:
                all_messages.extend(user_messages)
                user_list.append(user)

        if all_messages:
            success = self.send_push_notification_batch(all_messages)

            if success:
                # Create UserNotification records for each user
                for user in user_list:
                    self.create_breaking_news_record(user, article, breaking_news)

                # Update breaking news status
                breaking_news.is_sent = True
                breaking_news.sent_at = timezone.now()
                breaking_news.total_recipients = len(user_list)
                breaking_news.successful_deliveries = len(all_messages)
                breaking_news.save()

                self.stdout.write(self.style.SUCCESS(
                    f"Sent breaking news to {len(all_messages)} devices across {len(user_list)} users"
                ))
            else:
                self.stdout.write(self.style.ERROR("Failed to send breaking news"))

    def prepare_user_breaking_news(self, user, article, priority):
        """Prepare breaking news messages for a user"""
        push_tokens = PushToken.objects.filter(user=user, is_active=True)

        if not push_tokens:
            return []

        message = self.create_breaking_news_message(article, priority)
        messages = []

        for token in push_tokens:
            user_message = message.copy()
            user_message['token'] = token.token
            user_message['metadata'] = {
                'userId': str(user.id),
                'notificationType': 'breaking_news',
                'articleId': str(article.id),
                'priority': priority,
                'source': 'news_app'
            }
            messages.append(user_message)

        return messages

    def create_breaking_news_message(self, article, priority):
        """Create the breaking news notification message"""
        priority_icons = {
            'low': '📢',
            'medium': '🚨',
            'high': '🔥',
            'critical': '⚡'
        }

        icon = priority_icons.get(priority, '📢')

        return {
            'title': f'{icon} Breaking News: {article.source.name}',
            'body': article.title,
        }

    def create_breaking_news_record(self, user, article, breaking_news):
        """Create UserNotification and history record"""

        message = self.create_breaking_news_message(article, breaking_news.priority)

        # Create notification record
        user_notification = UserNotification.objects.create(
            user=user,
            notification_type='breaking_news',
            title=message['title'],
            body=message['body'],
            breaking_news=breaking_news,
            priority=breaking_news.priority,
            metadata={
                'article_id': article.id,
                'priority': breaking_news.priority
            }
        )

        # Link article
        user_notification.articles.add(article)

        # Create history entry
        ArticleNotificationHistory.objects.get_or_create(
            user=user,
            article=article,
            defaults={'notification': user_notification}
        )

        logger.info(f"Created breaking news record for user {user.username}, article {article.id}")

    def send_push_notification_batch(self, messages):
        """Send batch push notifications"""
        try:
            import requests

            api_url = 'http://notification-service:4000/api/push-notification'
            batch_size = 100
            success_count = 0

            for i in range(0, len(messages), batch_size):
                batch = messages[i:i + batch_size]

                response = requests.post(
                    api_url,
                    json={'messages': batch},
                    headers={'Content-Type': 'application/json'},
                    timeout=30
                )

                if response.status_code == 200:
                    success_count += len(batch)
                else:
                    logger.error(f"API error {response.status_code}: {response.text}")

            return success_count > 0

        except Exception as e:
            logger.error(f"Error sending notifications: {e}")
            return False