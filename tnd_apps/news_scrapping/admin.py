from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import (
    NewsSource, Category, Tag, Author, Article,
    ScrapingRun, ScrapingLog
)


@admin.register(NewsSource)
class NewsSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'base_url', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'base_url']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'article_count', 'created_at']
    search_fields = ['name']
    prepopulated_fields = {'slug': ('name',)}

    def article_count(self, obj):
        return obj.article_set.count()

    article_count.short_description = 'Articles'


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


class ScrapingLogInline(admin.TabularInline):
    model = ScrapingLog
    extra = 0
    readonly_fields = ['timestamp', 'level', 'message', 'article_url']
    can_delete = False


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = [
        'external_id','title_short', 'category', 'author', 'word_count',
        'has_full_content', 'scraped_at', 'view_article', 'source'
    ]
    list_filter = [
        'source', 'category', 'has_full_content', 'scraped_at', 'tags'
    ]
    search_fields = ['title', 'content', 'author__name']
    readonly_fields = ['external_id', 'url', 'scraped_at', 'updated_at']
    filter_horizontal = ['tags']
    date_hierarchy = 'scraped_at'

    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'slug', 'url', 'external_id', 'source')
        }),
        ('Content', {
            'fields': ('excerpt', 'content', 'word_count', 'paragraph_count')
        }),
        ('Media', {
            'fields': ('featured_image_url', 'image_caption')
        }),
        ('Classification', {
            'fields': ('category', 'author', 'tags')
        }),
        ('Metadata', {
            'fields': ('published_time_str', 'scraped_at', 'updated_at', 'has_full_content')
        }),
    )

    def title_short(self, obj):
        return obj.title[:50] + "..." if len(obj.title) > 50 else obj.title

    title_short.short_description = 'Title'

    def view_article(self, obj):
        return format_html(
            '<a href="{}" target="_blank">View Original</a>',
            obj.url
        )

    view_article.short_description = 'Original'


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