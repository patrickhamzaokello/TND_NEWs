from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    ArticleCitation,
    ArticleClaim,
    ArticleEnrichment,
    DailyDigest,
    DigestSubscriber,
    Entity,
    EntityMention,
    EnrichmentRun,
    SourcePerspective,
    StoryAlert,
    StoryCluster,
    StoryClusterArticle,
    StoryTimelineEvent,
)


@admin.register(DigestSubscriber)
class DigestSubscriberAdmin(admin.ModelAdmin):
    list_display = (
        'email', 'name', 'frequency', 'is_active', 'confirmed',
        'emails_sent', 'last_sent_at', 'last_slot_sent', 'subscribed_at',
    )
    list_filter = ('frequency', 'is_active', 'confirmed')
    search_fields = ('email', 'name')
    ordering = ('-subscribed_at',)
    readonly_fields = (
        'unsubscribe_token', 'subscribed_at', 'updated_at',
        'emails_sent', 'last_sent_at', 'last_digest_date', 'last_slot_sent',
    )
    actions = ['activate', 'deactivate', 'confirm_subscribers', 'send_test_morning', 'send_test_evening']

    fieldsets = (
        ('Subscriber', {
            'fields': ('email', 'name', 'user'),
        }),
        ('Schedule', {
            'fields': ('frequency', 'is_active', 'confirmed', 'confirmed_at'),
            'description': (
                '<b>morning_evening</b> — morning digest + 6 PM roundup (default)<br>'
                '<b>daily</b> — morning digest only'
            ),
        }),
        ('Delivery history', {
            'fields': ('emails_sent', 'last_sent_at', 'last_digest_date', 'last_slot_sent'),
            'classes': ('collapse',),
        }),
        ('Token', {
            'fields': ('unsubscribe_token', 'subscribed_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    # ── Bulk actions ───────────────────────────────────────────────────────────

    @admin.action(description='Activate selected subscribers')
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} subscriber(s) activated.')

    @admin.action(description='Deactivate selected subscribers')
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} subscriber(s) deactivated.')

    @admin.action(description='Mark selected as confirmed')
    def confirm_subscribers(self, request, queryset):
        updated = queryset.filter(confirmed=False).update(
            confirmed=True, confirmed_at=timezone.now()
        )
        self.message_user(request, f'{updated} subscriber(s) confirmed.')

    @admin.action(description='Send test morning digest to selected')
    def send_test_morning(self, request, queryset):
        from .models import DailyDigest
        from .email_service import send_digest_to_email

        digest = DailyDigest.objects.filter(
            digest_date=timezone.localdate(), is_published=True
        ).first()
        if not digest:
            self.message_user(request, "No published digest for today — cannot send morning test.", level='error')
            return

        sent = failed = 0
        for sub in queryset:
            if send_digest_to_email(digest, sub.email):
                sent += 1
            else:
                failed += 1
        self.message_user(request, f'Morning test: sent={sent} failed={failed}.')

    @admin.action(description='Send test evening roundup to selected')
    def send_test_evening(self, request, queryset):
        from .email_service import send_flash_to_email

        sent = failed = 0
        for sub in queryset:
            if send_flash_to_email('evening', sub.email):
                sent += 1
            else:
                failed += 1
        self.message_user(request, f'Evening test: sent={sent} failed={failed}.')


@admin.register(ArticleEnrichment)
class ArticleEnrichmentAdmin(admin.ModelAdmin):
    list_display = (
        'article_title', 'status', 'sentiment', 'importance_score',
        'follow_up_worthy', 'controversy_flag', 'has_editorial_image',
        'analyzed_at', 'token_cost',
    )
    list_filter = (
        'status', 'sentiment', 'follow_up_worthy',
        'controversy_flag', 'is_breaking_candidate',
    )
    search_fields = ('article__title', 'summary')
    readonly_fields = (
        'article', 'analyzed_at', 'input_tokens_used',
        'output_tokens_used', 'model_used', 'created_at', 'updated_at',
        'editorial_image_preview', 'editorial_image_generated_at',
    )
    ordering = ('-analyzed_at',)
    actions = ['action_generate_editorial_images']

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
        ('Editorial Image', {
            'fields': ('editorial_image', 'editorial_image_preview', 'editorial_image_generated_at'),
            'description': (
                'AI-generated engraving-style image. '
                'Use the <b>Generate editorial images</b> action on the list view to create one, '
                'or upload your own image here.'
            ),
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

    def has_editorial_image(self, obj):
        if obj.editorial_image:
            return format_html('<span style="color:green;font-weight:bold;">✓</span>')
        return format_html('<span style="color:#ccc;">—</span>')
    has_editorial_image.short_description = 'Editorial img'

    def editorial_image_preview(self, obj):
        if obj.editorial_image:
            return format_html(
                '<img src="{}" style="max-width:400px;max-height:400px;border-radius:4px;" />',
                obj.editorial_image.url,
            )
        return '(not generated yet)'
    editorial_image_preview.short_description = 'Preview'

    @admin.action(description='Generate editorial images (AI engraving) for selected articles')
    def action_generate_editorial_images(self, request, queryset):
        from .tasks import generate_editorial_images_batch

        # Filter to enrichments that have a source image
        ids = list(
            queryset.filter(
                article__featured_image_url__gt='',
            ).values_list('pk', flat=True)
        )
        no_image = queryset.count() - len(ids)

        if not ids:
            self.message_user(
                request,
                'None of the selected articles have a featured image — cannot generate.',
                level='warning',
            )
            return

        generate_editorial_images_batch.delay(ids)

        msg = f'Queued editorial image generation for {len(ids)} article(s).'
        if no_image:
            msg += f' {no_image} skipped (no featured image).'
        self.message_user(request, msg)


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
