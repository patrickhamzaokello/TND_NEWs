from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.core.management import call_command
from .models import (
    NewsSource, Category, Tag, Author, Article,
    ScrapingRun, ScrapingLog, UserProfile, ArticleView, 
    Comment, PushToken, ScheduledNotification, BreakingNews, 
    NotificationTemplate,ArticleNotificationHistory,UserNotification
)


@admin.register(NewsSource)
class NewsSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'base_url', 'is_active', 'created_at', 'follower_count', 'notification_count']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'base_url']

    def follower_count(self, obj):
        return obj.userprofile_set.count()
    follower_count.short_description = 'Followers'

    def notification_count(self, obj):
        return obj.article_set.filter(breaking_news__isnull=False).count()
    notification_count.short_description = 'Breaking News'


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'article_count', 'notification_count', 'created_at']
    search_fields = ['name']
    prepopulated_fields = {'slug': ('name',)}

    def article_count(self, obj):
        return obj.article_set.count()
    article_count.short_description = 'Articles'

    def notification_count(self, obj):
        return ScheduledNotification.objects.filter(include_categories=obj).count()
    notification_count.short_description = 'Scheduled Notifs'


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'article_count', 'created_at']
    search_fields = ['name']
    prepopulated_fields = {'slug': ('name',)}

    def article_count(self, obj):
        return obj.article_set.count()
    article_count.short_description = 'Articles'


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ['name', 'source', 'article_count', 'created_at']
    list_filter = ['source', 'created_at']
    search_fields = ['name']

    def article_count(self, obj):
        return obj.article_set.count()
    article_count.short_description = 'Articles'


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'followed_sources_count', 'preferred_categories_count', 'scheduled_notifications_count', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__username']
    filter_horizontal = ['followed_sources', 'preferred_categories']

    def followed_sources_count(self, obj):
        return obj.followed_sources.count()
    followed_sources_count.short_description = 'Sources Followed'

    def preferred_categories_count(self, obj):
        return obj.preferred_categories.count()
    preferred_categories_count.short_description = 'Categories Preferred'

    def scheduled_notifications_count(self, obj):
        return obj.user.scheduled_notifications.filter(is_active=True).count()
    scheduled_notifications_count.short_description = 'Active Notifs'


@admin.register(ArticleView)
class ArticleViewAdmin(admin.ModelAdmin):
    list_display = ['user', 'article_title', 'viewed_at', 'duration_seconds']
    list_filter = ['viewed_at', 'user']
    search_fields = ['user__username', 'article__title']
    readonly_fields = ['viewed_at']

    def article_title(self, obj):
        return obj.article.title[:50] + "..." if len(obj.article.title) > 50 else obj.article.title
    article_title.short_description = 'Article'


class ScrapingLogInline(admin.TabularInline):
    model = ScrapingLog
    extra = 0
    readonly_fields = ['timestamp', 'level', 'message', 'article_url']
    can_delete = False


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = [
        'external_id', 'title_short', 'category', 'author', 'word_count',
        'read_time_minutes', 'has_full_content', 'scraped_at', 'published_at',
        'view_count', 'breaking_news_status', 'view_article', 'source',
        'send_breaking_news_action'
    ]
    list_filter = [
        'source', 'category', 'has_full_content', 'scraped_at', 'tags',
        'breaking_news__is_sent'
    ]
    search_fields = ['title', 'content', 'author__name']
    readonly_fields = ['external_id', 'url', 'scraped_at', 'updated_at', 'published_at']
    filter_horizontal = ['tags']
    date_hierarchy = 'scraped_at'
    actions = ['send_as_breaking_news', 'mark_as_breaking_news']

    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'slug', 'url', 'external_id', 'source')
        }),
        ('Content', {
            'fields': ('excerpt', 'content', 'word_count', 'paragraph_count', 'read_time_minutes')
        }),
        ('Media', {
            'fields': ('featured_image_url', 'image_caption')
        }),
        ('Classification', {
            'fields': ('category', 'author', 'tags')
        }),
        ('Metadata', {
            'fields': ('published_time_str', 'published_at', 'scraped_at', 'updated_at', 'has_full_content')
        }),
    )

    def title_short(self, obj):
        return obj.title[:50] + "..." if len(obj.title) > 50 else obj.title
    title_short.short_description = 'Title'

    def view_count(self, obj):
        return obj.views.count()
    view_count.short_description = 'Views'

    def breaking_news_status(self, obj):
        if hasattr(obj, 'breaking_news'):
            breaking_news = obj.breaking_news
            if breaking_news.is_sent:
                return format_html(
                    '<span style="color: green;">✓ Sent ({})</span>',
                    breaking_news.priority
                )
            else:
                return format_html(
                    '<span style="color: orange;">Pending ({})</span>',
                    breaking_news.priority
                )
        return format_html('<span style="color: gray;">—</span>')
    breaking_news_status.short_description = 'Breaking News'

    def send_breaking_news_action(self, obj):
        if hasattr(obj, 'breaking_news') and not obj.breaking_news.is_sent:
            return format_html(
                '<a class="button" href="{}">Send Now</a>',
                reverse('admin:send_breaking_news', args=[obj.id])
            )
        return format_html('<span style="color: gray;">—</span>')
    send_breaking_news_action.short_description = 'Actions'

    def send_as_breaking_news(self, request, queryset):
        for article in queryset:
            call_command('send_breaking_news', f'--article-id={article.id}')
        self.message_user(request, f"Sent {queryset.count()} articles as breaking news")
    send_as_breaking_news.short_description = "Send as breaking news"

    def mark_as_breaking_news(self, request, queryset):
        from .models import BreakingNews
        count = 0
        for article in queryset:
            breaking_news, created = BreakingNews.objects.get_or_create(
                article=article,
                defaults={'priority': 'high'}
            )
            if created:
                count += 1
        self.message_user(request, f"Marked {count} articles as breaking news")
    mark_as_breaking_news.short_description = "Mark as breaking news (don't send)"

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/send-breaking-news/',
                self.admin_site.admin_view(self.send_breaking_news_view),
                name='send_breaking_news',
            ),
        ]
        return custom_urls + urls

    def send_breaking_news_view(self, request, object_id):
        from django.shortcuts import redirect
        try:
            article = Article.objects.get(id=object_id)
            call_command('send_breaking_news', f'--article-id={article.id}')
            self.message_user(request, f"Breaking news sent for: {article.title}")
        except Article.DoesNotExist:
            self.message_user(request, "Article not found", level='error')
        return redirect('../')

    def view_article(self, obj):
        return format_html(
            '<a href="{}" target="_blank">View Original</a>',
            obj.url
        )
    view_article.short_description = 'Original'


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['user', 'article', 'content', 'is_approved', 'created_at']
    actions = ['approve_comments']

    def approve_comments(self, request, queryset):
        queryset.update(is_approved=True)
    approve_comments.short_description = "Approve selected comments"


@admin.register(ScrapingRun)
class ScrapingRunAdmin(admin.ModelAdmin):
    list_display = [
        'run_id', 'source', 'status', 'articles_found',
        'articles_added', 'articles_updated', 'duration_seconds',
        'started_at'
    ]
    list_filter = ['source', 'status', 'scheduled_run', 'started_at']
    search_fields = ['run_id', 'error_message']
    readonly_fields = [
        'run_id', 'started_at', 'completed_at', 'duration_seconds'
    ]
    inlines = [ScrapingLogInline]

    fieldsets = (
        ('Run Information', {
            'fields': ('run_id', 'source', 'status', 'task_id', 'scheduled_run')
        }),
        ('Statistics', {
            'fields': (
                'articles_found', 'articles_added', 'articles_updated',
                'articles_skipped', 'error_count'
            )
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at', 'duration_seconds')
        }),
        ('Errors', {
            'fields': ('error_message',)
        }),
    )


@admin.register(ScrapingLog)
class ScrapingLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'run_short', 'level', 'message_short', 'article_url']
    list_filter = ['level', 'timestamp', 'run__source']
    search_fields = ['message', 'article_url']
    readonly_fields = ['timestamp']

    def run_short(self, obj):
        return str(obj.run.run_id)[:8]
    run_short.short_description = 'Run'

    def message_short(self, obj):
        return obj.message[:100] + "..." if len(obj.message) > 100 else obj.message
    message_short.short_description = 'Message'


@admin.register(PushToken)
class PushTokenAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'token_short', 'platform', 'is_active',
        'last_used', 'created_at', 'notification_count'
    ]
    list_filter = ['platform', 'is_active', 'created_at', 'last_used']
    search_fields = ['user__username', 'token', 'device_id']
    readonly_fields = ['created_at', 'updated_at', 'last_used']
    actions = ['mark_inactive', 'mark_active', 'test_token']

    fieldsets = (
        ('Token Information', {
            'fields': ('user', 'token', 'device_id', 'platform', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'last_used')
        }),
    )

    def token_short(self, obj):
        return obj.token[:20] + "..." if len(obj.token) > 20 else obj.token

    token_short.short_description = 'Token'

    def notification_count(self, obj):
        """Count notifications sent to this user"""
        return obj.user.notifications.count()

    notification_count.short_description = 'Notifs Sent'

    def mark_inactive(self, request, queryset):
        """Mark selected tokens as inactive"""
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Marked {updated} token(s) as inactive")

    mark_inactive.short_description = "Mark as inactive"

    def mark_active(self, request, queryset):
        """Mark selected tokens as active"""
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Marked {updated} token(s) as active")

    mark_active.short_description = "Mark as active"

    def test_token(self, request, queryset):
        """Send a test notification to selected tokens"""
        import requests
        from django.contrib import messages

        api_url = 'http://notification-service:4000/api/push-notification'
        success_count = 0
        error_count = 0

        for token_obj in queryset:
            if not token_obj.is_active:
                messages.warning(request, f"Skipped inactive token for {token_obj.user.username}")
                continue

            try:
                message = {
                    'token': token_obj.token,
                    'title': '🔔 Test Notification',
                    'body': f'This is a test notification for {token_obj.user.username}',
                    'metadata': {
                        'userId': str(token_obj.user.id),
                        'notificationType': 'test',
                        'source': 'admin_panel'
                    }
                }

                response = requests.post(
                    api_url,
                    json={'messages': [message]},
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )

                if response.status_code == 200:
                    success_count += 1
                else:
                    error_count += 1
                    messages.error(
                        request,
                        f"Failed to send to {token_obj.user.username}: {response.status_code}"
                    )
            except Exception as e:
                error_count += 1
                messages.error(request, f"Error sending to {token_obj.user.username}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Successfully sent {success_count} test notification(s)")
        if error_count > 0:
            messages.error(request, f"Failed to send {error_count} test notification(s)")

    test_token.short_description = "Send test notification"


@admin.register(ScheduledNotification)
class ScheduledNotificationAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'frequency', 'scheduled_time', 'next_send_at',
        'is_active', 'last_sent_at', 'max_articles', 'created_at',
        'notification_count'
    ]
    list_filter = ['frequency', 'is_active', 'created_at', 'scheduled_time']
    search_fields = ['user__username']
    readonly_fields = ['next_send_at', 'last_sent_at', 'created_at', 'updated_at']
    filter_horizontal = ['include_categories', 'include_sources']
    actions = ['send_now', 'enable_notifications', 'disable_notifications', 'recalculate_next_send']

    fieldsets = (
        ('User Settings', {
            'fields': ('user', 'is_active')
        }),
        ('Schedule', {
            'fields': ('frequency', 'scheduled_time', 'next_send_at', 'last_sent_at')
        }),
        ('Content Preferences', {
            'fields': ('max_articles', 'include_categories', 'include_sources')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def notification_count(self, obj):
        """Count of notifications sent through this schedule"""
        return obj.sent_notifications.count()

    notification_count.short_description = 'Sent Count'

    def send_now(self, request, queryset):
        """Send selected notifications immediately"""
        from django.core.management import call_command
        from django.contrib import messages

        success_count = 0
        error_count = 0

        for notification in queryset:
            if not notification.is_active:
                messages.warning(
                    request,
                    f"Skipped inactive notification for {notification.user.username}"
                )
                continue

            try:
                call_command('send_scheduled_notifications', f'--notification-id={notification.id}')
                success_count += 1
            except Exception as e:
                error_count += 1
                messages.error(request, f"Error sending to {notification.user.username}: {str(e)}")

        if success_count > 0:
            messages.success(request, f"Successfully sent {success_count} notification(s)")
        if error_count > 0:
            messages.error(request, f"Failed to send {error_count} notification(s)")

    send_now.short_description = "Send selected notifications now"

    def enable_notifications(self, request, queryset):
        """Enable selected notifications"""
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Enabled {updated} notification(s)")

    enable_notifications.short_description = "Enable selected notifications"

    def disable_notifications(self, request, queryset):
        """Disable selected notifications"""
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Disabled {updated} notification(s)")

    disable_notifications.short_description = "Disable selected notifications"

    def recalculate_next_send(self, request, queryset):
        """Recalculate next send time for selected notifications"""
        from django.utils import timezone

        for notification in queryset:
            notification.calculate_next_send()
            notification.save()

        self.message_user(
            request,
            f"Recalculated next send time for {queryset.count()} notification(s)"
        )

    recalculate_next_send.short_description = "Recalculate next send time"


@admin.register(BreakingNews)
class BreakingNewsAdmin(admin.ModelAdmin):
    list_display = [
        'article_title', 'priority', 'is_sent', 'sent_at', 
        'total_recipients', 'successful_deliveries', 'created_at',
        'send_action'
    ]
    list_filter = ['priority', 'is_sent', 'created_at', 'sent_at']
    search_fields = ['article__title', 'article__source__name']
    readonly_fields = ['sent_at', 'created_at', 'total_recipients', 'successful_deliveries', 'failed_deliveries']
    filter_horizontal = ['target_categories', 'target_sources']
    actions = ['send_breaking_news', 'mark_as_unsent']

    def article_title(self, obj):
        return obj.article.title[:60] + "..." if len(obj.article.title) > 60 else obj.article.title
    article_title.short_description = 'Article'

    def send_action(self, obj):
        if not obj.is_sent:
            return format_html(
                '<a class="button" href="{}">Send Now</a>',
                reverse('admin:send_breaking_news_direct', args=[obj.id])
            )
        return format_html('<span style="color: gray;">✓ Sent</span>')
    send_action.short_description = 'Action'

    def send_breaking_news(self, request, queryset):
        from django.core.management import call_command
        count = 0
        for breaking_news in queryset:
            if not breaking_news.is_sent:
                call_command('send_breaking_news', f'--breaking-news-id={breaking_news.id}')
                count += 1
        self.message_user(request, f"Sent {count} breaking news notifications")
    send_breaking_news.short_description = "Send selected breaking news"

    def mark_as_unsent(self, request, queryset):
        queryset.update(is_sent=False, sent_at=None)
        self.message_user(request, f"Marked {queryset.count()} as unsent")
    mark_as_unsent.short_description = "Mark as unsent (for resending)"

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/send-now/',
                self.admin_site.admin_view(self.send_breaking_news_direct),
                name='send_breaking_news_direct',
            ),
        ]
        return custom_urls + urls

    def send_breaking_news_direct(self, request, object_id):
        from django.shortcuts import redirect
        try:
            breaking_news = BreakingNews.objects.get(id=object_id)
            call_command('send_breaking_news', f'--breaking-news-id={breaking_news.id}')
            self.message_user(request, f"Breaking news sent: {breaking_news.article.title}")
        except BreakingNews.DoesNotExist:
            self.message_user(request, "Breaking news entry not found", level='error')
        return redirect('../')


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'notification_type', 'is_active', 'preview_title', 'preview_body']
    list_filter = ['notification_type', 'is_active']
    search_fields = ['name', 'title_template', 'body_template']

    def preview_title(self, obj):
        return obj.title_template[:50] + "..." if len(obj.title_template) > 50 else obj.title_template
    preview_title.short_description = 'Title Preview'

    def preview_body(self, obj):
        return obj.body_template[:100] + "..." if len(obj.body_template) > 100 else obj.body_template
    preview_body.short_description = 'Body Preview'


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user', 'notification_type', 'title_short',
        'is_read', 'sent_at', 'article_count', 'time_since_sent'
    ]
    list_filter = ['notification_type', 'is_read', 'sent_at', 'priority']
    search_fields = ['user__username', 'title', 'body']
    readonly_fields = ['sent_at', 'read_at']
    date_hierarchy = 'sent_at'
    actions = ['mark_as_read', 'mark_as_unread', 'delete_old_notifications']
    filter_horizontal = ['articles']

    def title_short(self, obj):
        return obj.title[:50] + "..." if len(obj.title) > 50 else obj.title

    title_short.short_description = 'Title'

    def article_count(self, obj):
        return obj.articles.count()

    article_count.short_description = 'Articles'

    def time_since_sent(self, obj):
        from django.utils.timesince import timesince
        return timesince(obj.sent_at) + " ago"

    time_since_sent.short_description = 'Time Ago'

    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'notification_type', 'priority')
        }),
        ('Content', {
            'fields': ('title', 'body', 'articles')
        }),
        ('Status', {
            'fields': ('is_read', 'read_at', 'sent_at')
        }),
        ('Links', {
            'fields': ('scheduled_notification', 'breaking_news')
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        })
    )

    def mark_as_read(self, request, queryset):
        """Mark selected notifications as read"""
        from django.utils import timezone

        updated = queryset.filter(is_read=False).update(
            is_read=True,
            read_at=timezone.now()
        )
        self.message_user(request, f"Marked {updated} notification(s) as read")

    mark_as_read.short_description = "Mark as read"

    def mark_as_unread(self, request, queryset):
        """Mark selected notifications as unread"""
        updated = queryset.update(is_read=False, read_at=None)
        self.message_user(request, f"Marked {updated} notification(s) as unread")

    mark_as_unread.short_description = "Mark as unread"

    def delete_old_notifications(self, request, queryset):
        """Delete notifications older than 30 days"""
        from django.utils import timezone
        from datetime import timedelta

        cutoff_date = timezone.now() - timedelta(days=30)
        old_notifications = queryset.filter(sent_at__lt=cutoff_date)
        count = old_notifications.count()
        old_notifications.delete()

        self.message_user(request, f"Deleted {count} old notification(s)")

    delete_old_notifications.short_description = "Delete notifications >30 days old"

@admin.register(ArticleNotificationHistory)
class ArticleNotificationHistoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'article_title', 'notification_type', 'sent_at']
    list_filter = ['sent_at', 'notification__notification_type']
    search_fields = ['user__username', 'article__title']
    readonly_fields = ['sent_at']
    date_hierarchy = 'sent_at'
    actions = ['delete_old_history']

    def article_title(self, obj):
        return obj.article.title[:50] + "..." if len(obj.article.title) > 50 else obj.article.title

    article_title.short_description = 'Article'

    def notification_type(self, obj):
        return obj.notification.notification_type if obj.notification else "—"

    notification_type.short_description = 'Type'

    def delete_old_history(self, request, queryset):
        """Delete history entries older than 30 days"""
        from django.utils import timezone
        from datetime import timedelta

        cutoff_date = timezone.now() - timedelta(days=30)
        old_history = queryset.filter(sent_at__lt=cutoff_date)
        count = old_history.count()
        old_history.delete()

        self.message_user(request, f"Deleted {count} old history entry(ies)")

    delete_old_history.short_description = "Delete history >30 days old"
