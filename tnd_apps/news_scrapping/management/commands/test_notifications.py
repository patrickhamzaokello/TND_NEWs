# management/commands/test_notifications.py
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from tnd_apps.news_scrapping.models import PushToken
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = 'Send test notifications to verify the notification system'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='Send test notification to specific user by ID'
        )
        parser.add_argument(
            '--username',
            type=str,
            help='Send test notification to specific user by username'
        )
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='Send test notification to all users with push tokens'
        )

    def handle(self, *args, **options):
        if options.get('user_id'):
            self.send_to_user_by_id(options['user_id'])
        elif options.get('username'):
            self.send_to_user_by_username(options['username'])
        elif options.get('all_users'):
            self.send_to_all_users()
        else:
            self.stdout.write(self.style.ERROR(
                'Please specify --user-id, --username, or --all-users'
            ))

    def send_to_user_by_id(self, user_id):
        """Send test notification to a specific user by ID"""
        try:
            user = User.objects.get(id=user_id)
            self.send_test_notification(user)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User with ID {user_id} not found'))

    def send_to_user_by_username(self, username):
        """Send test notification to a specific user by username"""
        try:
            user = User.objects.get(username=username)
            self.send_test_notification(user)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User {username} not found'))

    def send_to_all_users(self):
        """Send test notification to all users with active push tokens"""
        users = User.objects.filter(
            push_tokens__is_active=True
        ).distinct()

        self.stdout.write(f'Sending test notifications to {users.count()} users...')

        success_count = 0
        error_count = 0

        for user in users:
            try:
                self.send_test_notification(user)
                success_count += 1
            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(
                    f'Error sending to {user.username}: {str(e)}'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'Sent {success_count} notifications, {error_count} errors'
        ))

    def send_test_notification(self, user):
        """Send a test notification to a specific user"""
        push_tokens = PushToken.objects.filter(user=user, is_active=True)

        if not push_tokens.exists():
            self.stdout.write(self.style.WARNING(
                f'No active push tokens for user {user.username}'
            ))
            return

        messages = []
        for token in push_tokens:
            message = {
                'token': token.token,
                'title': '🔔 Test Notification',
                'body': f'Hello {user.username}! This is a test notification from your news app.',
                'metadata': {
                    'userId': str(user.id),
                    'notificationType': 'test',
                    'source': 'test_command'
                }
            }
            messages.append(message)

        # Send the notification
        success = self.send_push_notification_batch(messages)

        if success:
            self.stdout.write(self.style.SUCCESS(
                f'✓ Sent test notification to {user.username} '
                f'({len(messages)} device(s))'
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f'✗ Failed to send test notification to {user.username}'
            ))

    def send_push_notification_batch(self, messages):
        """Send batch push notifications"""
        try:
            import requests

            api_url = 'http://notification-service:4000/api/push-notification'

            response = requests.post(
                api_url,
                json={'messages': messages},
                headers={'Content-Type': 'application/json'},
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f'Successfully sent {len(messages)} test notifications')
                return True
            else:
                logger.error(f'API error {response.status_code}: {response.text}')
                return False

        except Exception as e:
            logger.error(f'Error sending test notifications: {str(e)}')
            return False


# management/commands/notification_stats.py
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from tnd_apps.news_scrapping.models import (
    UserNotification, ArticleNotificationHistory,
    PushToken, ScheduledNotification, BreakingNews
)

User = get_user_model()


class Command(BaseCommand):
    help = 'Display notification system statistics'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of days to analyze (default: 7)'
        )

    def handle(self, *args, **options):
        days = options['days']
        cutoff_date = timezone.now() - timedelta(days=days)

        self.stdout.write(self.style.SUCCESS(
            f'\n=== Notification System Statistics (Last {days} days) ==='
        ))

        # User statistics
        total_users = User.objects.count()
        users_with_tokens = User.objects.filter(
            push_tokens__is_active=True
        ).distinct().count()
        users_with_schedules = User.objects.filter(
            scheduled_notifications__is_active=True
        ).distinct().count()

        self.stdout.write('\n📱 User Statistics:')
        self.stdout.write(f'  Total users: {total_users}')
        self.stdout.write(f'  Users with active push tokens: {users_with_tokens}')
        self.stdout.write(f'  Users with active schedules: {users_with_schedules}')

        # Push token statistics
        total_tokens = PushToken.objects.count()
        active_tokens = PushToken.objects.filter(is_active=True).count()
        tokens_by_platform = PushToken.objects.filter(
            is_active=True
        ).values('platform').annotate(count=Count('id'))

        self.stdout.write('\n🔐 Push Token Statistics:')
        self.stdout.write(f'  Total tokens: {total_tokens}')
        self.stdout.write(f'  Active tokens: {active_tokens}')
        for item in tokens_by_platform:
            platform = item['platform'] or 'unknown'
            self.stdout.write(f'    {platform}: {item["count"]}')

        # Notification statistics
        total_notifications = UserNotification.objects.filter(
            sent_at__gte=cutoff_date
        ).count()

        notifications_by_type = UserNotification.objects.filter(
            sent_at__gte=cutoff_date
        ).values('notification_type').annotate(count=Count('id'))

        read_notifications = UserNotification.objects.filter(
            sent_at__gte=cutoff_date,
            is_read=True
        ).count()

        self.stdout.write('\n📬 Notification Statistics:')
        self.stdout.write(f'  Total notifications sent: {total_notifications}')
        self.stdout.write(f'  Read notifications: {read_notifications}')
        if total_notifications > 0:
            read_rate = (read_notifications / total_notifications) * 100
            self.stdout.write(f'  Read rate: {read_rate:.1f}%')

        self.stdout.write('\n  By type:')
        for item in notifications_by_type:
            self.stdout.write(f'    {item["notification_type"]}: {item["count"]}')

        # Article history statistics
        unique_articles_sent = ArticleNotificationHistory.objects.filter(
            sent_at__gte=cutoff_date
        ).values('article').distinct().count()

        total_sends = ArticleNotificationHistory.objects.filter(
            sent_at__gte=cutoff_date
        ).count()

        self.stdout.write('\n📰 Article Statistics:')
        self.stdout.write(f'  Unique articles sent: {unique_articles_sent}')
        self.stdout.write(f'  Total article sends: {total_sends}')
        if unique_articles_sent > 0:
            avg_sends = total_sends / unique_articles_sent
            self.stdout.write(f'  Avg sends per article: {avg_sends:.1f}')

        # Scheduled notification statistics
        active_schedules = ScheduledNotification.objects.filter(is_active=True)
        schedules_by_frequency = active_schedules.values(
            'frequency'
        ).annotate(count=Count('id'))

        self.stdout.write('\n⏰ Scheduled Notifications:')
        self.stdout.write(f'  Active schedules: {active_schedules.count()}')
        for item in schedules_by_frequency:
            self.stdout.write(f'    {item["frequency"]}: {item["count"]}')

        # Breaking news statistics
        breaking_news_sent = BreakingNews.objects.filter(
            sent_at__gte=cutoff_date
        ).count()

        breaking_news_pending = BreakingNews.objects.filter(
            is_sent=False
        ).count()

        self.stdout.write('\n🚨 Breaking News:')
        self.stdout.write(f'  Sent in last {days} days: {breaking_news_sent}')
        self.stdout.write(f'  Pending: {breaking_news_pending}')

        # Top users by notifications received
        top_users = User.objects.filter(
            notifications__sent_at__gte=cutoff_date
        ).annotate(
            notification_count=Count('notifications')
        ).order_by('-notification_count')[:5]

        if top_users.exists():
            self.stdout.write('\n👤 Top 5 Users by Notifications Received:')
            for user in top_users:
                self.stdout.write(
                    f'  {user.username}: {user.notification_count} notifications'
                )

        self.stdout.write('\n' + '=' * 60 + '\n')