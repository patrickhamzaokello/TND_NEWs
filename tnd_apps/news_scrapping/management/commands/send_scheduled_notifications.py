from django.core.management.base import BaseCommand
from django.utils import timezone
from tnd_apps.news_scrapping.models import (
    ScheduledNotification, Article, PushToken, UserProfile,
    UserNotification, ArticleNotificationHistory
)
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send scheduled news update notifications to users'

    def handle(self, *args, **options):
        logger.info("Starting scheduled notifications task")
        now = timezone.now()

        due_notifications = ScheduledNotification.objects.filter(
            next_send_at__lte=now,
            is_active=True
        ).select_related('user').prefetch_related(
            'include_categories', 'include_sources'
        )

        logger.info(f"Found {due_notifications.count()} due notifications")

        sent_count = 0
        error_count = 0
        all_messages = []
        notification_data = []  # Store data for creating UserNotification records

        for notification in due_notifications:
            logger.info(f"Processing notification {notification.id} for user {notification.user.username}")
            try:
                result = self.prepare_user_notification(notification)
                if result:
                    messages, articles, base_message = result
                    logger.info(f"Prepared {len(messages)} messages for user {notification.user.username}")
                    all_messages.extend(messages)
                    notification_data.append({
                        'notification': notification,
                        'messages': messages,
                        'articles': articles,
                        'base_message': base_message
                    })
                else:
                    logger.warning(f"No messages prepared for notification {notification.id}")

            except Exception as e:
                logger.error(f"Error preparing notification {notification.id}: {str(e)}", exc_info=True)
                error_count += 1

        if all_messages:
            logger.info(f"Sending batch of {len(all_messages)} notifications")
            success = self.send_push_notification_batch(all_messages)

            if success:
                # Create UserNotification records and update history
                for data in notification_data:
                    try:
                        self.create_user_notification_record(
                            data['notification'],
                            data['articles'],
                            data['base_message']
                        )

                        # Update notification schedule
                        data['notification'].last_sent_at = now
                        data['notification'].calculate_next_send()
                        data['notification'].save()
                        sent_count += len(data['messages'])
                    except Exception as e:
                        logger.error(f"Error creating notification record: {str(e)}", exc_info=True)

                logger.info(f"Successfully sent {sent_count} notifications")
            else:
                logger.error("Failed to send notification batch")
        else:
            logger.warning("No messages to send")

        self.stdout.write(
            self.style.SUCCESS(
                f"Sent {sent_count} notifications, {error_count} errors"
            )
        )

    def prepare_user_notification(self, notification):
        """Prepare notification messages for a user"""

        # Get articles, excluding ones already sent to this user
        articles = self.get_recent_articles(notification)

        if not articles:
            logger.info(f"No new articles for user {notification.user.username}")
            return None

        push_tokens = PushToken.objects.filter(
            user=notification.user,
            is_active=True
        )

        if not push_tokens:
            logger.warning(f"No active push tokens for user {notification.user.username}")
            return None

        messages = []
        base_message = self.create_notification_message(articles, notification)

        for token in push_tokens:
            message = base_message.copy()
            message['token'] = token.token
            message['metadata'] = {
                'userId': str(notification.user.id),
                'notificationType': 'scheduled_digest',
                'articleCount': len(articles),
                'articleIds': [str(article.id) for article in articles],
                'source': 'news_app'
            }
            messages.append(message)

        return (messages, articles, base_message)

    def get_recent_articles(self, notification):
        """Get recent articles, excluding those already sent to the user"""
        now = timezone.now()

        # Determine time window
        if notification.last_sent_at:
            since_time = notification.last_sent_at
        else:
            since_time = now - (
                timedelta(days=7) if notification.frequency == 'weekly'
                else timedelta(hours=24)
            )

        # Get base queryset
        queryset = Article.objects.filter(
            scraped_at__gt=since_time,
            is_processed=False
        )

        # Exclude articles already sent to this user (within last 7 days)
        cutoff_date = now - timedelta(days=7)
        already_sent_ids = ArticleNotificationHistory.objects.filter(
            user=notification.user,
            sent_at__gte=cutoff_date
        ).values_list('article_id', flat=True)

        queryset = queryset.exclude(id__in=already_sent_ids)

        logger.info(f"Excluded {len(already_sent_ids)} already-sent articles for user {notification.user.username}")

        # Apply user preferences
        try:
            user_profile = UserProfile.objects.get(user=notification.user)

            # Category filtering
            if notification.include_categories.exists():
                queryset = queryset.filter(category__in=notification.include_categories.all())
            elif user_profile.preferred_categories.exists():
                queryset = queryset.filter(category__in=user_profile.preferred_categories.all())

            # Source filtering
            if notification.include_sources.exists():
                queryset = queryset.filter(source__in=notification.include_sources.all())
            elif user_profile.followed_sources.exists():
                queryset = queryset.filter(source__in=user_profile.followed_sources.all())

        except UserProfile.DoesNotExist:
            logger.warning(f"No user profile for {notification.user.username}")

            if notification.include_categories.exists():
                queryset = queryset.filter(category__in=notification.include_categories.all())
            if notification.include_sources.exists():
                queryset = queryset.filter(source__in=notification.include_sources.all())

        articles = list(queryset.order_by('-scraped_at')[:notification.max_articles])
        logger.info(f"Selected {len(articles)} new articles for user {notification.user.username}")

        return articles

    def create_notification_message(self, articles, notification):
        """Create the notification message content"""

        if len(articles) == 1:
            article = articles[0]
            return {
                "title": f"🔥 Hot from {article.source.name}",
                "body": f"{article.title} — tap to get the full story!",
            }
        else:
            source_names = ', '.join(set(article.source.name for article in articles[:3]))
            return {
                "title": "📢 Your Daily News Fix",
                "body": f"{len(articles)} must-read stories from {source_names} — don't miss out!",
            }

    def create_user_notification_record(self, scheduled_notification, articles, base_message):
        """Create UserNotification record and history entries"""

        # Create the notification record
        user_notification = UserNotification.objects.create(
            user=scheduled_notification.user,
            notification_type='scheduled_digest',
            title=base_message['title'],
            body=base_message['body'],
            scheduled_notification=scheduled_notification,
            metadata={
                'frequency': scheduled_notification.frequency,
                'article_count': len(articles)
            }
        )

        # Link articles
        user_notification.articles.set(articles)

        # Create history entries for each article
        history_entries = [
            ArticleNotificationHistory(
                user=scheduled_notification.user,
                article=article,
                notification=user_notification
            )
            for article in articles
        ]
        ArticleNotificationHistory.objects.bulk_create(
            history_entries,
            ignore_conflicts=True  # Handle race conditions
        )

        logger.info(f"Created notification record {user_notification.id} with {len(articles)} articles")

        return user_notification

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
                    timeout=10
                )

                if response.status_code == 200:
                    success_count += len(batch)
                else:
                    logger.error(f"API error {response.status_code}: {response.text}")

            return success_count > 0

        except Exception as e:
            logger.error(f"Error sending notifications: {str(e)}", exc_info=True)
            return False