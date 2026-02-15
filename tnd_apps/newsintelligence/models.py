from django.db import models
from django.utils import timezone
from django.contrib.postgres.fields import ArrayField


class ArticleEnrichment(models.Model):
    """
    Silver layer: AI-enriched version of a raw Article.
    Linked 1-to-1 with the existing Article model.
    Only articles with has_full_content=True are eligible.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),  # e.g. too short, duplicate
    ]

    SENTIMENT_CHOICES = [
        ('positive', 'Positive'),
        ('negative', 'Negative'),
        ('neutral', 'Neutral'),
        ('mixed', 'Mixed'),
    ]

    # Link to raw article — using string ref to avoid circular import
    article = models.OneToOneField(
        'news_scrapping.Article',   # <-- replace 'news' with your actual app name
        on_delete=models.CASCADE,
        related_name='enrichment'
    )

    # Processing state
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)

    # ── Core AI outputs ───────────────────────────────────────────────────────

    summary = models.TextField(blank=True, help_text="2-3 sentence neutral summary")

    sentiment = models.CharField(
        max_length=10, choices=SENTIMENT_CHOICES, blank=True
    )
    sentiment_score = models.FloatField(
        null=True, blank=True,
        help_text="Float from -1.0 (very negative) to 1.0 (very positive)"
    )

    importance_score = models.IntegerField(
        null=True, blank=True,
        help_text="Newsworthiness score 1-10"
    )

    # Stored as JSON arrays
    themes = models.JSONField(
        default=list, blank=True,
        help_text="e.g. ['governance', 'entertainment', 'economy']"
    )
    key_facts = models.JSONField(
        default=list, blank=True,
        help_text="Bullet-point facts extracted from the article"
    )
    related_themes = models.JSONField(
        default=list, blank=True,
        help_text="Broader story threads this article connects to"
    )

    # ── Entity extraction ─────────────────────────────────────────────────────

    entities_people = models.JSONField(
        default=list, blank=True,
        help_text="Named persons mentioned"
    )
    entities_organizations = models.JSONField(
        default=list, blank=True,
        help_text="Organizations/companies mentioned"
    )
    entities_locations = models.JSONField(
        default=list, blank=True,
        help_text="Places mentioned"
    )

    # ── Audience & flags ──────────────────────────────────────────────────────

    audience_business = models.FloatField(
        null=True, blank=True, help_text="0.0 to 1.0 relevance for business readers"
    )
    audience_general = models.FloatField(
        null=True, blank=True, help_text="0.0 to 1.0 relevance for general public"
    )
    audience_government = models.FloatField(
        null=True, blank=True, help_text="0.0 to 1.0 relevance for government/policy readers"
    )
    audience_youth = models.FloatField(
        null=True, blank=True, help_text="0.0 to 1.0 relevance for youth readers"
    )

    follow_up_worthy = models.BooleanField(
        default=False, help_text="Should this story be tracked for follow-up?"
    )
    controversy_flag = models.BooleanField(
        default=False, help_text="Does this article contain controversial content?"
    )
    is_breaking_candidate = models.BooleanField(
        default=False, help_text="AI thinks this warrants a breaking news flag"
    )

    # ── Token tracking (for cost monitoring) ─────────────────────────────────

    input_tokens_used = models.IntegerField(default=0)
    output_tokens_used = models.IntegerField(default=0)
    model_used = models.CharField(max_length=60, blank=True)

    # ── Timestamps ────────────────────────────────────────────────────────────

    created_at = models.DateTimeField(auto_now_add=True)
    analyzed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Enrichment [{self.status}] → {self.article.title[:60]}"

    class Meta:
        db_table = 'article_enrichments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['sentiment']),
            models.Index(fields=['importance_score']),
            models.Index(fields=['analyzed_at']),
            models.Index(fields=['follow_up_worthy']),
        ]


class EntityMention(models.Model):
    """
    Tracks every named entity across enriched articles.
    Powers trend detection: who/what is dominating coverage.
    """

    ENTITY_TYPES = [
        ('person', 'Person'),
        ('organization', 'Organization'),
        ('location', 'Location'),
    ]

    enrichment = models.ForeignKey(
        ArticleEnrichment,
        on_delete=models.CASCADE,
        related_name='entity_mentions'
    )
    entity_name = models.CharField(max_length=200, db_index=True)
    entity_type = models.CharField(max_length=20, choices=ENTITY_TYPES)
    mention_date = models.DateField(db_index=True)
    sentiment_score = models.FloatField(
        null=True, blank=True,
        help_text="Sentiment in context of this entity within the article"
    )
    context_snippet = models.TextField(
        blank=True,
        help_text="Short excerpt showing how entity was mentioned"
    )

    def __str__(self):
        return f"{self.entity_type}: {self.entity_name} ({self.mention_date})"

    class Meta:
        db_table = 'entity_mentions'
        indexes = [
            models.Index(fields=['entity_name', 'mention_date']),
            models.Index(fields=['entity_type', 'mention_date']),
        ]


class DailyDigest(models.Model):
    """
    Gold layer: synthesized daily intelligence brief.
    One record per day, generated by the DigestAgent.
    """

    digest_date = models.DateField(unique=True, db_index=True)

    # The full AI-generated narrative
    digest_text = models.TextField(blank=True)

    # Structured data for programmatic use
    top_stories = models.JSONField(
        default=list, blank=True,
        help_text="[{article_id, title, why_it_matters, importance}, ...]"
    )
    trending_entities = models.JSONField(
        default=list, blank=True,
        help_text="[{entity, type, mention_count, sentiment_trend}, ...]"
    )
    sector_sentiment = models.JSONField(
        default=dict, blank=True,
        help_text="{governance: -0.2, entertainment: 0.8, economy: 0.1, ...}"
    )
    story_threads = models.JSONField(
        default=list, blank=True,
        help_text="Ongoing multi-day stories to watch"
    )
    under_radar_story = models.JSONField(
        default=dict, blank=True,
        help_text="One article that deserves more attention"
    )
    key_concern = models.TextField(
        blank=True,
        help_text="One data point that should concern decision-makers"
    )

    # Stats for this digest
    articles_analyzed = models.IntegerField(default=0)
    input_tokens_used = models.IntegerField(default=0)
    output_tokens_used = models.IntegerField(default=0)
    model_used = models.CharField(max_length=60, blank=True)

    # Generation state
    is_published = models.BooleanField(default=False)
    generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Daily Digest — {self.digest_date}"

    class Meta:
        db_table = 'daily_digests'
        ordering = ['-digest_date']


class EnrichmentRun(models.Model):
    """
    Audit trail for every enrichment batch run.
    Mirrors the pattern of your existing ScrapingRun model.
    """

    STATUS_CHOICES = [
        ('started', 'Started'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('partial', 'Partially Completed'),
    ]

    RUN_TYPE_CHOICES = [
        ('enrichment', 'Article Enrichment'),
        ('entity_extraction', 'Entity Extraction'),
        ('daily_digest', 'Daily Digest'),
        ('retry', 'Retry Failed'),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='started')
    run_type = models.CharField(max_length=30, choices=RUN_TYPE_CHOICES, default='enrichment')

    # Stats
    articles_found = models.IntegerField(default=0)
    articles_processed = models.IntegerField(default=0)
    articles_failed = models.IntegerField(default=0)
    articles_skipped = models.IntegerField(default=0)

    # Token cost tracking
    total_input_tokens = models.IntegerField(default=0)
    total_output_tokens = models.IntegerField(default=0)
    estimated_cost_usd = models.DecimalField(
        max_digits=10, decimal_places=6, default=0
    )

    # Timing
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    # Error tracking
    error_message = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        if self.completed_at and self.started_at:
            self.duration_seconds = (
                self.completed_at - self.started_at
            ).total_seconds()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"EnrichmentRun [{self.run_type}] {self.status} @ {self.started_at}"

    class Meta:
        db_table = 'enrichment_runs'
        ordering = ['-started_at']
