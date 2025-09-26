from django.core.management.base import BaseCommand
from django.utils import timezone
from tnd_apps.news_scrapping.models import ScheduledNotification, Article, PushToken, UserProfile
from datetime import timedelta
import logging
import json

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Send scheduled news update notifications to users'
    
    def handle(self, *args, **options):
        logger.info("Starting scheduled notifications task")
        now = timezone.now()
        
        logger.info(f"Current time: {now} (tz: {now.tzinfo})")
        due_notifications = ScheduledNotification.objects.filter(
            next_send_at__lte=now,
            is_active=True
        ).select_related('user').prefetch_related(
            'include_categories', 'include_sources'
        )
        
        logger.info(f"Found {due_notifications.count()} due notifications")
        if not due_notifications.exists():
            logger.warning("No due notifications found. Check next_send_at and is_active.")
        
        sent_count = 0
        error_count = 0
        all_messages = []
        notification_records = []
        
        for notification in due_notifications:
            logger.info(f"Processing notification {notification.id} for user {notification.user.username}")
            try:
                user_messages = self.prepare_user_notification(notification)
                if user_messages:
                    logger.info(f"Prepared {len(user_messages)} messages for user {notification.user.username}")
                    all_messages.extend(user_messages)
                    notification_records.append(notification)
                else:
                    logger.warning(f"No messages prepared for notification {notification.id}")
                
            except Exception as e:
                logger.error(f"Error preparing notification {notification.id} for {notification.user.username}: {str(e)}", exc_info=True)
                error_count += 1
        
        if all_messages:
            logger.info(f"Sending batch of {len(all_messages)} notifications")
            success = self.send_push_notification_batch(all_messages)
            
            if success:
                for notification in notification_records:
                    logger.debug(f"Updating notification {notification.id} for user {notification.user.username}")
                    notification.last_sent_at = now
                    notification.calculate_next_send()
                    notification.save()
                    sent_count += len([m for m in all_messages if m.get('metadata', {}).get('userId') == str(notification.user.id)])
                logger.info(f"Successfully updated {len(notification_records)} notification records")
            else:
                logger.error("Failed to send notification batch")
        else:
            logger.warning("No messages to send")
        
        logger.info(f"Completed task: Sent {sent_count} notifications to {len(notification_records)} users, {error_count} errors")
        self.stdout.write(
            self.style.SUCCESS(
                f"Sent {sent_count} notifications to {len(notification_records)} users, {error_count} errors"
            )
        )
    
    def prepare_user_notification(self, notification):
        """Prepare notification messages for a user"""
        logger.debug(f"Preparing notification for user {notification.user.username}, notification ID {notification.id}")
        
        articles = self.get_recent_articles(notification)
        
        if not articles:
            logger.info(f"No new articles found for user {notification.user.username}")
            return []
        
        logger.debug(f"Found {len(articles)} articles for notification {notification.id}: {[a.title for a in articles]}")
        
        push_tokens = PushToken.objects.filter(
            user=notification.user,
            is_active=True
        )
        
        if not push_tokens:
            logger.warning(f"No active push tokens for user {notification.user.username}")
            return []
        
        logger.debug(f"Found {push_tokens.count()} active push tokens for user {notification.user.username}")
        
        messages = []
        base_message = self.create_notification_message(articles, notification)
        
        for token in push_tokens:
            logger.debug(f"Preparing message for token {token.token[:20]}... on platform {token.platform}")
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
        
        return messages
    
    def get_recent_articles(self, notification):
        """Get recent articles based on user preferences"""
        logger.info(f"Fetching recent articles for notification {notification.id} (user: {notification.user.username})")
        now = timezone.now()
        if notification.last_sent_at:
            since_time = notification.last_sent_at
            logger.info(f"Using last_sent_at: {since_time} (tz: {since_time.tzinfo})")
        else:
            since_time = now - (timedelta(days=7) if notification.frequency == 'weekly' else timedelta(hours=24))
            logger.info(f"Using default window: {since_time} (tz: {since_time.tzinfo})")
        
        logger.info(f"Time window: articles scraped after {since_time}")
        
        # Log all recent articles before filtering
        all_recent_articles = Article.objects.filter(scraped_at__gt=since_time)
        logger.info(f"Found {all_recent_articles.count()} articles scraped after {since_time}")
        if all_recent_articles.exists():
            logger.info(f"Sample articles: {[f'{a.title} (scraped_at: {a.scraped_at}, is_processed: {a.is_processed})' for a in all_recent_articles[:3]]}")
        
        # Apply is_processed filter
        queryset = all_recent_articles.filter(is_processed=False)
        logger.info(f"After is_processed=False filter, found {queryset.count()} articles")
        
        try:
            user_profile = UserProfile.objects.get(user=notification.user)
            logger.info(f"Found user profile for {notification.user.username}")
            
            # Log user preferences
            if notification.include_categories.exists():
                categories = notification.include_categories.all()
                logger.debug(f"Notification-specific categories: {[c.name for c in categories]}")
                queryset = queryset.filter(category__in=categories)
                logger.debug(f"After category filter, found {queryset.count()} articles")
            elif user_profile.preferred_categories.exists():
                categories = user_profile.preferred_categories.all()
                logger.debug(f"User profile categories: {[c.name for c in categories]}")
                queryset = queryset.filter(category__in=categories)
                logger.debug(f"After category filter, found {queryset.count()} articles")
            else:
                logger.debug("No category filters applied")
            
            if notification.include_sources.exists():
                sources = notification.include_sources.all()
                logger.debug(f"Notification-specific sources: {[s.name for s in sources]}")
                queryset = queryset.filter(source__in=sources)
                logger.debug(f"After source filter, found {queryset.count()} articles")
            elif user_profile.followed_sources.exists():
                sources = user_profile.followed_sources.all()
                logger.debug(f"User profile sources: {[s.name for s in sources]}")
                queryset = queryset.filter(source__in=sources)
                logger.debug(f"After source filter, found {queryset.count()} articles")
            else:
                logger.debug("No source filters applied")
                
        except UserProfile.DoesNotExist:
            logger.warning(f"No user profile found for {notification.user.username}, using notification filters only")
            if notification.include_categories.exists():
                categories = notification.include_categories.all()
                logger.debug(f"Notification-specific categories: {[c.name for c in categories]}")
                queryset = queryset.filter(category__in=categories)
                logger.debug(f"After category filter, found {queryset.count()} articles")
            else:
                logger.debug("No notification-specific category filters")
            if notification.include_sources.exists():
                sources = notification.include_sources.all()
                logger.debug(f"Notification-specific sources: {[s.name for s in sources]}")
                queryset = queryset.filter(source__in=sources)
                logger.debug(f"After source filter, found {queryset.count()} articles")
            else:
                logger.debug("No notification-specific source filters")
        
        articles = queryset.order_by('-scraped_at')[:notification.max_articles]
        logger.debug(f"Final selection: {len(articles)} articles: {[f'{a.title} (scraped_at: {a.scraped_at})' for a in articles]}")
        return articles
    
    def create_notification_message(self, articles, notification):
        """Create the notification message content"""
        logger.debug(f"Creating notification message for {len(articles)} articles")
        
        if len(articles) == 1:
            article = articles[0]
            logger.debug(f"Single article notification: {article.title}")
            return {
                "title": f"🔥 Hot from {article.source.name}",
                "body": f"{article.title} — tap to get the full story!",
            }
        else:
            source_names = ', '.join(set(article.source.name for article in articles[:3]))
            logger.debug(f"Multi-article digest from sources: {source_names}")
            return {
                "title": "📢 Your Daily News Fix",
                "body": f"{len(articles)} must-read stories from {source_names} — don’t miss out!",
            }
    
    def send_push_notification_batch(self, messages):
        """Send batch push notifications using your API endpoint"""
        logger.info(f"Sending batch of {len(messages)} push notifications")
        try:
            import requests
            import json
            
            api_url = 'http://notification-service:4000/api/push-notification'
            
            batch_size = 100
            success_count = 0
            
            for i in range(0, len(messages), batch_size):
                batch = messages[i:i + batch_size]
                logger.debug(f"Sending batch of {len(batch)} messages")
                
                payload = {
                    'messages': batch
                }
                
                logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
                
                response = requests.post(
                    api_url,
                    json=payload,
                    headers={
                        'Content-Type': 'application/json',
                    },
                    timeout=10  # Reduced for internal network
                )
                
                if response.status_code == 200:
                    logger.info(f"Successfully sent batch of {len(batch)} notifications")
                    success_count += len(batch)
                else:
                    logger.error(f"API returned status {response.status_code}: {response.text}")
            
            logger.info(f"Batch send completed: {success_count} successful")
            return success_count > 0
            
        except Exception as e:
            logger.error(f"Error sending push notifications: {str(e)}", exc_info=True)
            return False
