import secrets

from django.db import models
from django.utils import timezone


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
    key_highlights = models.JSONField(
        default=list, blank=True,
        help_text=(
            'Phrases from the article body that clients should underline. '
            'Each item: {text, type, url?} where type is '
            'fact | figure | claim | link'
        ),
    )
    claims = models.JSONField(
        default=list, blank=True,
        help_text="Grounded claims with evidence and confidence"
    )
    citations = models.JSONField(
        default=list, blank=True,
        help_text="Source references used by AI outputs"
    )
    local_impact = models.JSONField(
        default=dict, blank=True,
        help_text="Uganda/local impact analysis by region, group, and time horizon"
    )
    bias_or_framing_notes = models.JSONField(
        default=list, blank=True,
        help_text="Observed framing or perspective notes for source-aware analysis"
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

    # ── Editorial image (AI-generated engraving style) ────────────────────────

    editorial_image = models.ImageField(
        upload_to='editorial_images/', null=True, blank=True,
        help_text='AI-generated editorial engraving version of the featured image',
    )
    editorial_image_generated_at = models.DateTimeField(null=True, blank=True)

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
    normalized_name = models.CharField(max_length=200, blank=True, db_index=True)
    salience = models.FloatField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.normalized_name:
            self.normalized_name = self.entity_name.lower().strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.entity_type}: {self.entity_name} ({self.mention_date})"

    class Meta:
        db_table = 'entity_mentions'
        indexes = [
            models.Index(fields=['entity_name', 'mention_date']),
            models.Index(fields=['entity_type', 'mention_date']),
            models.Index(fields=['normalized_name', 'mention_date']),
            models.Index(fields=['normalized_name', 'entity_type', 'mention_date']),
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
    citations = models.JSONField(
        default=list, blank=True,
        help_text="Source references used in digest_text and story summaries"
    )
    under_radar_story = models.JSONField(
        default=dict, blank=True,
        help_text="One article that deserves more attention"
    )
    key_concern = models.TextField(
        blank=True,
        help_text="One data point that should concern decision-makers"
    )
    key_concern_short = models.CharField(
        max_length=200,
        blank=True,
        help_text="Tweet-sized version of key_concern (≤180 chars, complete sentence)"
    )

    # Stats for this digest
    articles_analyzed = models.IntegerField(default=0)
    input_tokens_used = models.IntegerField(default=0)
    output_tokens_used = models.IntegerField(default=0)
    model_used = models.CharField(max_length=60, blank=True)

    # ── Digest illustration (AI editorial image based on top story) ───────────

    illustration = models.ImageField(
        upload_to='digest_illustrations/', null=True, blank=True,
        help_text='AI-generated editorial illustration for this digest',
    )
    illustration_caption = models.TextField(
        blank=True,
        help_text='One-sentence editorial caption for the illustration',
    )
    illustration_generated_at = models.DateTimeField(null=True, blank=True)

    # ── Social posting ────────────────────────────────────────────────────────
    twitter_thread_id = models.CharField(
        max_length=32, blank=True,
        help_text='Tweet ID of the first tweet in the posted thread',
    )
    twitter_posted_at = models.DateTimeField(null=True, blank=True)

    # Generation state
    is_published = models.BooleanField(default=False)
    editorial_review_status = models.CharField(
        max_length=20,
        choices=[
            ('draft', 'Draft'),
            ('needs_review', 'Needs Review'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='needs_review',
    )
    reviewed_by = models.CharField(max_length=120, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Daily Digest — {self.digest_date}"

    class Meta:
        db_table = 'daily_digests'
        ordering = ['-digest_date']
        indexes = [
            models.Index(fields=['is_published', '-digest_date']),
            models.Index(fields=['editorial_review_status', '-digest_date']),
        ]


class Entity(models.Model):
    """Canonical entity with aliases across sources and articles."""

    ENTITY_TYPES = EntityMention.ENTITY_TYPES

    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200, db_index=True)
    entity_type = models.CharField(max_length=20, choices=ENTITY_TYPES)
    aliases = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.normalized_name:
            self.normalized_name = self.name.lower().strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.entity_type})"

    class Meta:
        db_table = 'entities'
        unique_together = ['normalized_name', 'entity_type']
        indexes = [
            models.Index(fields=['entity_type', 'normalized_name']),
        ]


class StoryCluster(models.Model):
    """Groups related articles from multiple sources into one evolving story."""

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('dormant', 'Dormant'),
        ('resolved', 'Resolved'),
    ]

    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=320, unique=True)
    summary = models.TextField(blank=True)
    why_this_matters = models.TextField(blank=True)
    local_impact = models.JSONField(default=dict, blank=True)
    primary_theme = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    importance_score = models.IntegerField(default=0)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'story_clusters'
        ordering = ['-last_seen_at', '-importance_score']
        indexes = [
            models.Index(fields=['status', '-last_seen_at']),
            models.Index(fields=['primary_theme', '-last_seen_at']),
            models.Index(fields=['importance_score']),
        ]


class StoryClusterArticle(models.Model):
    cluster = models.ForeignKey(StoryCluster, on_delete=models.CASCADE, related_name='cluster_articles')
    article = models.ForeignKey('news_scrapping.Article', on_delete=models.CASCADE, related_name='story_cluster_links')
    relevance_score = models.FloatField(default=1.0)
    perspective_label = models.CharField(max_length=120, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_cluster_articles'
        unique_together = ['cluster', 'article']
        indexes = [
            models.Index(fields=['cluster', '-relevance_score']),
            models.Index(fields=['article']),
        ]


class StoryTimelineEvent(models.Model):
    cluster = models.ForeignKey(StoryCluster, on_delete=models.CASCADE, related_name='timeline_events')
    event_date = models.DateTimeField()
    title = models.CharField(max_length=240)
    description = models.TextField()
    article = models.ForeignKey('news_scrapping.Article', on_delete=models.SET_NULL, null=True, blank=True)
    citations = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_timeline_events'
        ordering = ['event_date']
        indexes = [
            models.Index(fields=['cluster', 'event_date']),
        ]


class SourcePerspective(models.Model):
    cluster = models.ForeignKey(StoryCluster, on_delete=models.CASCADE, related_name='source_perspectives')
    source = models.ForeignKey('news_scrapping.NewsSource', on_delete=models.CASCADE)
    article = models.ForeignKey('news_scrapping.Article', on_delete=models.CASCADE)
    framing_summary = models.TextField(blank=True)
    notable_emphasis = models.JSONField(default=list, blank=True)
    omitted_context = models.JSONField(default=list, blank=True)
    sentiment_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'source_perspectives'
        unique_together = ['cluster', 'source', 'article']
        indexes = [
            models.Index(fields=['cluster', 'source']),
        ]


class StoryAlert(models.Model):
    """Tracks high-signal updates that should trigger user alerts."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('suppressed', 'Suppressed'),
    ]

    cluster = models.ForeignKey(StoryCluster, on_delete=models.CASCADE, related_name='alerts')
    article = models.ForeignKey('news_scrapping.Article', on_delete=models.CASCADE)
    title = models.CharField(max_length=240)
    reason = models.TextField()
    importance_score = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'story_alerts'
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['cluster', '-created_at']),
        ]


class ArticleClaim(models.Model):
    article = models.ForeignKey('news_scrapping.Article', on_delete=models.CASCADE, related_name='claims')
    enrichment = models.ForeignKey(ArticleEnrichment, on_delete=models.SET_NULL, null=True, blank=True)
    claim_text = models.TextField()
    evidence_text = models.TextField(blank=True)
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'article_claims'
        indexes = [
            models.Index(fields=['article']),
            models.Index(fields=['confidence']),
        ]


class ArticleCitation(models.Model):
    article = models.ForeignKey('news_scrapping.Article', on_delete=models.CASCADE, related_name='citations')
    enrichment = models.ForeignKey(ArticleEnrichment, on_delete=models.SET_NULL, null=True, blank=True)
    url = models.URLField(max_length=500)
    title = models.CharField(max_length=500)
    source_name = models.CharField(max_length=120, blank=True)
    evidence_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'article_citations'
        indexes = [
            models.Index(fields=['article']),
            models.Index(fields=['source_name']),
        ]


class DigestSubscriber(models.Model):
    """
    Anyone who signs up to receive the TNDNEWS Daily Digest by email.

    Users do NOT need an account — a standalone email + unsubscribe token
    is sufficient. If they do have an account the `user` FK links them so
    we can show their subscription status in a profile page.
    """

    FREQUENCY_CHOICES = [
        ('morning_evening', 'Morning digest + evening articles (default)'),
        ('daily',           'Morning digest only'),
        ('breaking', 'Breaking news only'),
    ]

    SLOT_CHOICES = [
        ('morning', 'Morning'),
        ('midday',  'Midday'),
        ('evening', 'Evening'),
        ('night',   'Night'),
    ]

    email = models.EmailField(unique=True, db_index=True)
    name = models.CharField(max_length=120, blank=True, help_text="Display name in greeting")

    # Optional link to a registered user account
    user = models.OneToOneField(
        'authentication.User',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='digest_subscription',
    )

    frequency = models.CharField(
        max_length=20, choices=FREQUENCY_CHOICES, default='morning_evening'
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Set False on unsubscribe — keeps record for analytics"
    )

    # Unsubscribe token — sent in every email footer link
    unsubscribe_token = models.CharField(max_length=64, unique=True, editable=False)

    confirmed = models.BooleanField(
        default=False,
        help_text="True after the confirmation email is clicked"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # Delivery stats
    last_sent_at = models.DateTimeField(null=True, blank=True)
    emails_sent = models.PositiveIntegerField(default=0)
    last_digest_date = models.DateField(
        null=True, blank=True,
        help_text="Date of the last digest successfully delivered"
    )
    last_slot_sent = models.CharField(
        max_length=20, blank=True, default='',
        help_text="Slot name of the last email sent (morning/midday/evening/night)"
    )

    subscribed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.unsubscribe_token:
            self.unsubscribe_token = secrets.token_urlsafe(48)
        super().save(*args, **kwargs)

    def mark_sent(self, digest_date, slot: str = ''):
        self.last_sent_at = timezone.now()
        self.emails_sent += 1
        self.last_digest_date = digest_date
        self.last_slot_sent = slot
        self.save(update_fields=['last_sent_at', 'emails_sent', 'last_digest_date', 'last_slot_sent'])

    def __str__(self):
        status = 'active' if self.is_active else 'unsubscribed'
        return f"{self.email} ({status})"

    class Meta:
        db_table = 'digest_subscribers'
        ordering = ['-subscribed_at']
        indexes = [
            models.Index(fields=['is_active', 'confirmed']),
            models.Index(fields=['last_sent_at']),
        ]


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
