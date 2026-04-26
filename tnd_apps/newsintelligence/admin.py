from django.contrib import admin
from django.utils.html import format_html

from .models import (
    ArticleCitation,
    ArticleClaim,
    ArticleEnrichment,
    DailyDigest,
    Entity,
    EntityMention,
    EnrichmentRun,
    SourcePerspective,
    StoryAlert,
    StoryCluster,
    StoryClusterArticle,
    StoryTimelineEvent,
)


@admin.register(ArticleEnrichment)
class ArticleEnrichmentAdmin(admin.ModelAdmin):
    list_display = (
        'article_title', 'status', 'sentiment', 'importance_score',
        'follow_up_worthy', 'controversy_flag', 'analyzed_at', 'token_cost'
    )
    list_filter = (
        'status', 'sentiment', 'follow_up_worthy',
        'controversy_flag', 'is_breaking_candidate'
    )
    search_fields = ('article__title', 'summary')
    readonly_fields = (
        'article', 'analyzed_at', 'input_tokens_used',
        'output_tokens_used', 'model_used', 'created_at', 'updated_at'
    )
    ordering = ('-analyzed_at',)

    fieldsets = (
        ('Article', {'fields': ('article', 'status', 'error_message', 'retry_count')}),
        ('AI Analysis', {
            'fields': (
                'summary', 'sentiment', 'sentiment_score', 'importance_score',
                'themes', 'key_facts', 'claims', 'citations',
                'local_impact', 'bias_or_framing_notes', 'related_themes',
            )
        }),
        ('Entities', {
            'fields': ('entities_people', 'entities_organizations', 'entities_locations'),
        }),
        ('Audience', {
            'fields': (
                'audience_business', 'audience_general',
                'audience_government', 'audience_youth',
            )
        }),
        ('Flags', {
            'fields': ('follow_up_worthy', 'controversy_flag', 'is_breaking_candidate'),
        }),
        ('Metadata', {
            'fields': ('input_tokens_used', 'output_tokens_used', 'model_used', 'analyzed_at'),
            'classes': ('collapse',),
        }),
    )

    def article_title(self, obj):
        return obj.article.title[:80]
    article_title.short_description = 'Article'

    def token_cost(self, obj):
        from .openai_client import calculate_cost, ENRICHMENT_MODEL
        cost = calculate_cost(
            obj.model_used or ENRICHMENT_MODEL,
            obj.input_tokens_used,
            obj.output_tokens_used,
        )
        return f'${cost:.5f}'
    token_cost.short_description = 'Est. Cost'


@admin.register(EntityMention)
class EntityMentionAdmin(admin.ModelAdmin):
    list_display = ('entity_name', 'entity_type', 'mention_date', 'sentiment_score')
    list_filter = ('entity_type', 'mention_date')
    search_fields = ('entity_name',)
    ordering = ('-mention_date', 'entity_name')


@admin.register(DailyDigest)
class DailyDigestAdmin(admin.ModelAdmin):
    list_display = (
        'digest_date', 'articles_analyzed', 'is_published',
        'editorial_review_status', 'generated_at', 'token_info'
    )
    list_filter = ('is_published', 'editorial_review_status')
    readonly_fields = ('generated_at', 'created_at')
    ordering = ('-digest_date',)

    def token_info(self, obj):
        return f'in:{obj.input_tokens_used:,} out:{obj.output_tokens_used:,}'
    token_info.short_description = 'Tokens'


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ('name', 'entity_type', 'updated_at')
    list_filter = ('entity_type',)
    search_fields = ('name', 'aliases')


class StoryClusterArticleInline(admin.TabularInline):
    model = StoryClusterArticle
    extra = 0


class StoryTimelineEventInline(admin.TabularInline):
    model = StoryTimelineEvent
    extra = 0


@admin.register(StoryCluster)
class StoryClusterAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'primary_theme', 'importance_score', 'last_seen_at')
    list_filter = ('status', 'primary_theme')
    search_fields = ('title', 'summary', 'why_this_matters')
    prepopulated_fields = {'slug': ('title',)}
    inlines = [StoryClusterArticleInline, StoryTimelineEventInline]


@admin.register(SourcePerspective)
class SourcePerspectiveAdmin(admin.ModelAdmin):
    list_display = ('cluster', 'source', 'article', 'sentiment_score', 'created_at')
    list_filter = ('source',)
    search_fields = ('cluster__title', 'article__title', 'framing_summary')


@admin.register(StoryAlert)
class StoryAlertAdmin(admin.ModelAdmin):
    list_display = ('title', 'cluster', 'importance_score', 'status', 'created_at', 'sent_at')
    list_filter = ('status', 'importance_score')
    search_fields = ('title', 'reason', 'cluster__title', 'article__title')


@admin.register(ArticleClaim)
class ArticleClaimAdmin(admin.ModelAdmin):
    list_display = ('article', 'confidence', 'created_at')
    search_fields = ('article__title', 'claim_text', 'evidence_text')


@admin.register(ArticleCitation)
class ArticleCitationAdmin(admin.ModelAdmin):
    list_display = ('article', 'source_name', 'created_at')
    search_fields = ('article__title', 'title', 'url', 'evidence_text')


@admin.register(EnrichmentRun)
class EnrichmentRunAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'run_type', 'status', 'articles_processed',
        'articles_failed', 'estimated_cost_usd', 'duration_seconds', 'started_at'
    )
    list_filter = ('run_type', 'status')
    readonly_fields = (
        'started_at', 'completed_at', 'duration_seconds',
        'total_input_tokens', 'total_output_tokens'
    )
    ordering = ('-started_at',)
