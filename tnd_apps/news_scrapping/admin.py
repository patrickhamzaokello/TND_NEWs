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
    list_display = ['user', 'token_short', 'platform', 'is_active', 'last_used', 'created_at', 'notification_count']
    list_filter = ['platform', 'is_active', 'created_at', 'last_used']
    search_fields = ['user__username', 'token', 'device_id']
    readonly_fields = ['created_at', 'updated_at', 'last_used']

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
        # This would need to be implemented based on your notification tracking
        return "—"
    notification_count.short_description = 'Notifs Sent'


@admin.register(ScheduledNotification)
class ScheduledNotificationAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'frequency', 'scheduled_time', 'next_send_at', 
        'is_active', 'last_sent_at', 'max_articles', 'created_at'
    ]
    list_filter = ['frequency', 'is_active', 'created_at', 'scheduled_time']
    search_fields = ['user__username']
    readonly_fields = ['next_send_at', 'last_sent_at', 'created_at', 'updated_at']
    filter_horizontal = ['include_categories', 'include_sources']
    actions = ['send_now', 'enable_notifications', 'disable_notifications']

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

    def send_now(self, request, queryset):
        from django.core.management import call_command
        count = 0
        for notification in queryset:
            # You'll need to implement this command or use a direct method
            call_command('send_scheduled_notifications', f'--notification-id={notification.id}')
            count += 1
        self.message_user(request, f"Sent {count} notifications immediately")
    send_now.short_description = "Send selected notifications now"

    def enable_notifications(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, f"Enabled {queryset.count()} notifications")
    enable_notifications.short_description = "Enable selected notifications"

    def disable_notifications(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f"Disabled {queryset.count()} notifications")
    disable_notifications.short_description = "Disable selected notifications"


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
        'id', 'user', 'notification_type', 'title',
        'is_read', 'sent_at', 'article_count'
    ]
    list_filter = ['notification_type', 'is_read', 'sent_at']
    search_fields = ['user__username', 'title', 'body']
    readonly_fields = ['sent_at', 'read_at']
    date_hierarchy = 'sent_at'

    def article_count(self, obj):
        return obj.articles.count()

    article_count.short_description = 'Articles'

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


@admin.register(ArticleNotificationHistory)
class ArticleNotificationHistoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'article_title', 'notification', 'sent_at']
    list_filter = ['sent_at']
    search_fields = ['user__username', 'article__title']
    readonly_fields = ['sent_at']
    date_hierarchy = 'sent_at'

    def article_title(self, obj):
        return obj.article.title[:50]

    article_title.short_description = 'Article'
