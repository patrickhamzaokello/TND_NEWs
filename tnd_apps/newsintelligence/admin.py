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
    StoryClusterRelation,
    StoryTimelineEvent,
    StoryVersion,
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
        'editorial_image_status_display', 'analyzed_at', 'token_cost',
    )
    list_filter = (
        'status', 'sentiment', 'follow_up_worthy',
        'controversy_flag', 'is_breaking_candidate', 'editorial_image_status',
    )
    search_fields = ('article__title', 'summary')
    readonly_fields = (
        'article', 'analyzed_at', 'input_tokens_used',
        'output_tokens_used', 'model_used', 'created_at', 'updated_at',
        'editorial_image_preview', 'editorial_image_generated_at',
        'editorial_image_last_attempt',
    )
    ordering = ('-analyzed_at',)
    actions = ['action_generate_editorial_images']

    fieldsets = (
        ('Article', {'fields': ('article', 'status', 'error_message', 'retry_count')}),
        ('AI Analysis', {
            'fields': (
                'summary', 'sentiment', 'sentiment_score', 'importance_score',
                'themes', 'key_facts', 'key_highlights', 'claims', 'citations',
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
            'fields': (
                'editorial_image', 'editorial_image_preview',
                'editorial_image_generated_at', 'editorial_image_last_attempt',
                'editorial_image_status', 'editorial_image_error',
            ),
            'description': (
                'AI-generated engraving-style image. '
                'Use the <b>Generate editorial images</b> action on the list view to create one.'
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

    def editorial_image_status_display(self, obj):
        status = obj.editorial_image_status
        if not status:
            return format_html('<span style="color:#aaa;">not attempted</span>')
        colors = {
            'generated': 'green',
            'skipped': '#888',
            'moderation': 'orange',
            'download_error': 'red',
            'api_error': 'red',
            'error': 'red',
        }
        color = colors.get(status, '#888')
        label = obj.get_editorial_image_status_display() or status
        tip = obj.editorial_image_error or ''
        if tip:
            return format_html(
                '<span style="color:{};" title="{}">{}</span>', color, tip[:200], label
            )
        return format_html('<span style="color:{};">{}</span>', color, label)
    editorial_image_status_display.short_description = 'Image status'

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
        'editorial_review_status', 'has_illustration', 'illustration_status_display',
        'twitter_status', 'generated_at', 'token_info',
    )
    list_filter = ('is_published', 'editorial_review_status', 'illustration_status')
    readonly_fields = (
        'generated_at', 'created_at',
        'illustration_preview', 'illustration_generated_at', 'illustration_last_attempt',
        'twitter_posted_at', 'twitter_thread_link',
    )
    ordering = ('-digest_date',)
    actions = ['action_generate_illustration', 'action_post_to_twitter']

    fieldsets = (
        (None, {
            'fields': (
                'digest_date', 'digest_text', 'key_concern',
                'top_stories', 'under_radar_story', 'trending_entities',
                'sector_sentiment', 'story_threads', 'citations',
            ),
        }),
        ('Illustration', {
            'fields': (
                'illustration', 'illustration_preview', 'illustration_caption',
                'illustration_generated_at', 'illustration_last_attempt',
                'illustration_status', 'illustration_error',
            ),
        }),
        ('Publishing', {
            'fields': ('is_published', 'editorial_review_status', 'reviewed_by', 'reviewed_at'),
        }),
        ('Social — Twitter / X', {
            'fields': ('twitter_thread_id', 'twitter_posted_at', 'twitter_thread_link'),
        }),
        ('Stats', {
            'fields': ('articles_analyzed', 'input_tokens_used', 'output_tokens_used', 'model_used', 'generated_at', 'created_at'),
            'classes': ('collapse',),
        }),
    )

    def token_info(self, obj):
        return f'in:{obj.input_tokens_used:,} out:{obj.output_tokens_used:,}'
    token_info.short_description = 'Tokens'

    def twitter_status(self, obj):
        if obj.twitter_thread_id:
            url = f'https://x.com/i/web/status/{obj.twitter_thread_id}'
            return format_html('<a href="{}" target="_blank" style="color:green;font-weight:bold;">✓ posted</a>', url)
        return format_html('<span style="color:#ccc;">—</span>')
    twitter_status.short_description = 'Twitter'

    def has_illustration(self, obj):
        if obj.illustration:
            return format_html('<span style="color:green;font-weight:bold;">✓</span>')
        return format_html('<span style="color:#ccc;">—</span>')
    has_illustration.short_description = 'Illus.'

    def illustration_status_display(self, obj):
        status = obj.illustration_status
        if not status:
            return format_html('<span style="color:#aaa;">not attempted</span>')
        colors = {
            'generated': 'green',
            'skipped': '#888',
            'moderation': 'orange',
            'download_error': 'red',
            'api_error': 'red',
            'error': 'red',
        }
        color = colors.get(status, '#888')
        label = obj.get_illustration_status_display() or status
        tip = obj.illustration_error or ''
        if tip:
            return format_html(
                '<span style="color:{};" title="{}">{}</span>', color, tip[:200], label
            )
        return format_html('<span style="color:{};">{}</span>', color, label)
    illustration_status_display.short_description = 'Illus. status'

    def illustration_preview(self, obj):
        if obj.illustration:
            return format_html(
                '<img src="{}" style="max-width:100%;max-height:480px;border-radius:6px;" />'
                '<p style="color:#666;font-size:12px;margin-top:6px;font-style:italic;">{}</p>',
                obj.illustration.url,
                obj.illustration_caption or '',
            )
        return '(not generated yet)'
    illustration_preview.short_description = 'Preview'

    def twitter_thread_link(self, obj):
        if obj.twitter_thread_id:
            url = f'https://x.com/i/web/status/{obj.twitter_thread_id}'
            return format_html('<a href="{}" target="_blank">View thread ↗</a>', url)
        return '(not posted yet)'
    twitter_thread_link.short_description = 'Thread link'

    @admin.action(description='Post selected digests to Twitter / X as a thread')
    def action_post_to_twitter(self, request, queryset):
        from .tasks import post_digest_to_twitter

        queued = skipped = 0
        for digest in queryset:
            if digest.twitter_thread_id:
                skipped += 1
                continue
            if not digest.is_published:
                skipped += 1
                continue
            post_digest_to_twitter.delay(digest.pk)
            queued += 1

        msg = f'Queued {queued} digest(s) for Twitter posting.'
        if skipped:
            msg += f' {skipped} skipped (already posted or not published).'
        self.message_user(request, msg)

    @admin.action(description='Generate digest illustration (AI editorial image)')
    def action_generate_illustration(self, request, queryset):
        from .tasks import generate_digest_illustration

        queued = 0
        for digest in queryset:
            generate_digest_illustration.delay(digest.pk)
            queued += 1
        self.message_user(request, f'Queued illustration generation for {queued} digest(s).')


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


class StoryVersionInline(admin.TabularInline):
    model = StoryVersion
    extra = 0
    fields = ('version', 'title', 'article_count', 'change_note', 'created_at')
    readonly_fields = ('version', 'title', 'article_count', 'change_note', 'created_at')
    can_delete = False
    ordering = ('-version',)


@admin.register(StoryCluster)
class StoryClusterAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'status', 'version', 'article_count_display',
        'primary_theme', 'importance_score', 'synthesized_at', 'last_seen_at',
    )
    list_filter = ('status', 'primary_theme', 'version')
    search_fields = ('title', 'summary', 'short_summary', 'why_this_matters')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('version', 'synthesized_at', 'articles_at_synthesis')
    exclude = ('centroid_embedding',)
    inlines = [StoryVersionInline, StoryClusterArticleInline, StoryTimelineEventInline]
    actions = ['action_synthesize']

    def article_count_display(self, obj):
        return obj.cluster_articles.count()
    article_count_display.short_description = 'Articles'

    @admin.action(description='Re-synthesize story (AI title + summary + highlights)')
    def action_synthesize(self, request, queryset):
        from .tasks import synthesize_story_task
        for cluster in queryset:
            synthesize_story_task.delay(cluster.pk, force=True)
        self.message_user(request, f'Queued synthesis for {queryset.count()} story(ies).')


@admin.register(StoryClusterRelation)
class StoryClusterRelationAdmin(admin.ModelAdmin):
    list_display = ('from_cluster', 'relation_type', 'to_cluster', 'note', 'created_at')
    list_filter = ('relation_type',)
    search_fields = ('from_cluster__title', 'to_cluster__title', 'note')
    ordering = ('-created_at',)


@admin.register(StoryVersion)
class StoryVersionAdmin(admin.ModelAdmin):
    list_display = ('cluster', 'version', 'title', 'article_count', 'change_note', 'created_at')
    search_fields = ('title', 'cluster__title')
    readonly_fields = (
        'cluster', 'version', 'title', 'short_summary', 'long_summary',
        'key_highlights', 'article_count', 'change_note', 'created_at',
    )
    ordering = ('-created_at',)


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
