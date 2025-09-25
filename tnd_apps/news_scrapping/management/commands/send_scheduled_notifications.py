# management/commands/send_scheduled_notifications.py (updated)
from django.core.management.base import BaseCommand
from django.utils import timezone
from tnd_apps.news_scrapping.models import ScheduledNotification, Article, PushToken, UserProfile
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
        
        # Collect all messages for batch sending
        all_messages = []
        notification_records = []
        
        for notification in due_notifications:
            try:
                user_messages = self.prepare_user_notification(notification)
                if user_messages:
                    all_messages.extend(user_messages)
                    notification_records.append(notification)
                
            except Exception as e:
                logger.error(f"Error preparing notification for {notification.user.username}: {e}")
                error_count += 1
        
        # Send all notifications in batch
        if all_messages:
            success = self.send_push_notification_batch(all_messages)
            
            if success:
                # Update notification records
                for notification in notification_records:
                    notification.last_sent_at = now
                    notification.calculate_next_send()
                    notification.save()
                    sent_count += len([m for m in all_messages if m.get('user_id') == str(notification.user.id)])
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Sent {sent_count} notifications to {len(notification_records)} users, {error_count} errors"
            )
        )
    
    def prepare_user_notification(self, notification):
        """Prepare notification messages for a user"""
        
        # Get recent articles based on user preferences
        articles = self.get_recent_articles(notification)
        
        if not articles:
            logger.info(f"No new articles found for {notification.user.username}")
            return []
        
        # Get user's active push tokens
        push_tokens = PushToken.objects.filter(
            user=notification.user,
            is_active=True
        )
        
        if not push_tokens:
            logger.info(f"No active push tokens for {notification.user.username}")
            return []
        
        # Create notification message for each token
        messages = []
        base_message = self.create_notification_message(articles, notification)
        
        for token in push_tokens:
            message = base_message.copy()
            message['token'] = token.token
            message['metadata'] = {
                'userId': str(notification.user.id),
                'notificationType': 'scheduled_digest',
                'articleCount': len(articles),
                'source': 'news_app'
            }
            messages.append(message)
        
        return messages
    
    def get_recent_articles(self, notification):
        """Get recent articles based on user preferences"""
        since_time = timezone.now() - timedelta(hours=24)
        
        queryset = Article.objects.filter(
            published_at__gte=since_time,
            is_processed=True
        )
        
        # Apply user preferences from UserProfile
        try:
            user_profile = UserProfile.objects.get(user=notification.user)
            
            # Use notification-specific filters or fall back to user profile
            if notification.include_categories.exists():
                queryset = queryset.filter(
                    category__in=notification.include_categories.all()
                )
            elif user_profile.preferred_categories.exists():
                queryset = queryset.filter(
                    category__in=user_profile.preferred_categories.all()
                )
            
            if notification.include_sources.exists():
                queryset = queryset.filter(
                    source__in=notification.include_sources.all()
                )
            elif user_profile.followed_sources.exists():
                queryset = queryset.filter(
                    source__in=user_profile.followed_sources.all()
                )
                
        except UserProfile.DoesNotExist:
            # If no user profile, use notification filters only
            if notification.include_categories.exists():
                queryset = queryset.filter(
                    category__in=notification.include_categories.all()
                )
            if notification.include_sources.exists():
                queryset = queryset.filter(
                    source__in=notification.include_sources.all()
                )
        
        return queryset.order_by('-published_at')[:notification.max_articles]
    
    def create_notification_message(self, articles, notification):
        """Create the notification message content"""
        
        if len(articles) == 1:
            article = articles[0]
            return {
                'title': f'📰 {article.source.name}',
                'body': article.title,
            }
        else:
            source_names = ', '.join(set(article.source.name for article in articles[:3]))
            return {
                'title': '📰 Your News Digest',
                'body': f'{len(articles)} new stories from {source_names}',
            }
    
    def send_push_notification_batch(self, messages):
        """Send batch push notifications using your API endpoint"""
        try:
            import requests
            import json
            
            api_url = 'http://notification-service:4000/api/push-notification'
            
            # Split into batches of 100 to avoid overwhelming the API
            batch_size = 100
            success_count = 0
            
            for i in range(0, len(messages), batch_size):
                batch = messages[i:i + batch_size]
                
                payload = {
                    'messages': batch
                }
                
                response = requests.post(
                    api_url,
                    json=payload,
                    headers={
                        'Content-Type': 'application/json',
                    },
                    timeout=30
                )
                
                if response.status_code == 200:
                    logger.info(f"Successfully sent batch of {len(batch)} notifications")
                    success_count += len(batch)
                else:
                    logger.error(f"API returned status {response.status_code}: {response.text}")
            
            return success_count > 0
            
        except Exception as e:
            logger.error(f"Error sending push notifications: {e}")
            return False
