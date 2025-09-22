# management/commands/send_scheduled_notifications.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from news_scrapping.models import ScheduledNotification, Article, PushToken
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Send scheduled news update notifications to users'
    
    def handle(self, *args, **options):
        now = timezone.now()
        
        # Get notifications due to be sent
        due_notifications = ScheduledNotification.objects.filter(
            next_send_at__lte=now,
            is_active=True
        ).select_related('user').prefetch_related(
            'include_categories', 'include_sources'
        )
        
        sent_count = 0
        error_count = 0
        
        for notification in due_notifications:
            try:
                self.send_notification(notification)
                notification.last_sent_at = now
                notification.calculate_next_send()
                notification.save()
                sent_count += 1
                
            except Exception as e:
                logger.error(f"Error sending notification to {notification.user.username}: {e}")
                error_count += 1
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Sent {sent_count} notifications, {error_count} errors"
            )
        )
    
    def send_notification(self, notification):
        """Send a single notification to a user"""
        
        # Get recent articles based on user preferences
        articles = self.get_recent_articles(notification)
        
        if not articles:
            logger.info(f"No new articles found for {notification.user.username}")
            return
        
        # Get user's active push tokens
        push_tokens = PushToken.objects.filter(
            user=notification.user,
            is_active=True
        )
        
        if not push_tokens:
            logger.info(f"No active push tokens for {notification.user.username}")
            return
        
        # Create notification message
        message = self.create_notification_message(articles, notification)
        
        # Send to each device
        for token in push_tokens:
            self.send_push_notification(token.token, message)
    
    def get_recent_articles(self, notification):
        """Get recent articles based on user preferences"""
        
        # Articles from last 24 hours
        since_time = timezone.now() - timedelta(hours=24)
        
        queryset = Article.objects.filter(
            published_at__gte=since_time,
            is_processed=True
        )
        
        # Apply category filters if specified
        if notification.include_categories.exists():
            queryset = queryset.filter(
                category__in=notification.include_categories.all()
            )
        
        # Apply source filters if specified
        if notification.include_sources.exists():
            queryset = queryset.filter(
                source__in=notification.include_sources.all()
            )
        
        # If no specific preferences, use user's followed sources and categories
        if not notification.include_categories.exists() and not notification.include_sources.exists():
            # You might want to use UserProfile preferences here
            pass
        
        return queryset.order_by('-published_at')[:notification.max_articles]
    
    def create_notification_message(self, articles, notification):
        """Create the notification message content"""
        
        if len(articles) == 1:
            article = articles[0]
            return {
                'title': f'📰 {article.source.name}',
                'body': article.title,
                'data': {
                    'type': 'article',
                    'article_id': str(article.id),
                    'url': article.url
                }
            }
        else:
            source_names = ', '.join(set(article.source.name for article in articles[:3]))
            return {
                'title': '📰 Your Daily News Digest',
                'body': f'{len(articles)} new stories from {source_names}',
                'data': {
                    'type': 'digest',
                    'article_count': len(articles)
                }
            }
    
    def send_push_notification(self, token, message):
        """Send push notification using Expo or your preferred service"""
        
        # Example using Expo
        try:
            import requests
            
            response = requests.post(
                'https://exp.host/--/api/v2/push/send',
                json={
                    'to': token,
                    'title': message['title'],
                    'body': message['body'],
                    'data': message.get('data', {}),
                    'sound': 'default',
                    'badge': 1,
                },
                headers={
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                }
            )
            
            if response.status_code != 200:
                logger.error(f"Push notification failed: {response.text}")
                
        except ImportError:
            logger.warning("Expo SDK not installed, skipping push notification")
        except Exception as e:
            logger.error(f"Error sending push notification: {e}")
