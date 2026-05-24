from django.db import models
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.utils import timezone
from django.core.exceptions import ValidationError
import uuid
import hashlib
import re
from dateutil import parser
from django.conf import settings
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .text_cleaning import clean_article_text


class NewsSource(models.Model):
    """Model to track different news sources"""
    RELIABILITY_CHOICES = [
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
        ('unknown', 'Unknown'),
    ]

    name = models.CharField(max_length=100, unique=True)
    base_url = models.URLField()
    news_url = models.URLField()
    is_active = models.BooleanField(default=True)
    reliability_tier = models.CharField(max_length=20, choices=RELIABILITY_CHOICES, default='unknown')
    ownership = models.CharField(max_length=200, blank=True)
    editorial_notes = models.TextField(blank=True)
    favicon_url = models.URLField(blank=True, max_length=500)
    country = models.CharField(max_length=80, default='Uganda')
    language = models.CharField(max_length=40, default='English')
    scrape_config = models.JSONField(default=dict, blank=True)
    last_successful_scrape_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'news_sources'



class Category(models.Model):
    """Model for news categories"""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'categories'
        verbose_name_plural = 'Categories'


class UserProfile(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='user_profiles',
    )
    followed_sources = models.ManyToManyField(NewsSource, blank=True)
    preferred_categories = models.ManyToManyField(Category, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Profile for {self.user.username}"

class Tag(models.Model):
    """Model for news tags"""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'tags'


class Author(models.Model):
    """Model for news authors"""
    name = models.CharField(max_length=200)
    profile_url = models.URLField(blank=True, null=True)
    source = models.ForeignKey(NewsSource, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.source.name})"

    class Meta:
        db_table = 'authors'
        unique_together = ['name', 'source']


class Article(models.Model):
    """Main model for news articles"""

    SCRAPE_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('partial', 'Partial'),
        ('complete', 'Complete'),
        ('failed', 'Failed'),
    ]

    # Unique identifier and basic info
    external_id = models.CharField(max_length=50, db_index=True)  # post-46006
    url = models.URLField(unique=True, max_length=500)
    canonical_url = models.URLField(max_length=500, blank=True)
    source_published_id = models.CharField(max_length=120, blank=True)
    title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=200, blank=True)
    normalized_title_hash = models.CharField(max_length=64, blank=True)

    # Content
    excerpt = models.TextField(blank=True)
    content = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    search_vector = SearchVectorField(null=True, blank=True)
    word_count = models.IntegerField(default=0)
    paragraph_count = models.IntegerField(default=0)

    # Media
    featured_image_url = models.URLField(blank=True, max_length=500)
    image_caption = models.TextField(blank=True)

    # Relationships
    source = models.ForeignKey(NewsSource, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    author = models.ForeignKey(Author, on_delete=models.SET_NULL, null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True)

    # Timestamps
    published_time_str = models.CharField(max_length=100, blank=True)  # "7 hours ago"
    published_at = models.DateTimeField(null=True, blank=True)
    scraped_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Status
    is_processed = models.BooleanField(default=False)
    has_full_content = models.BooleanField(default=False)
    scrape_status = models.CharField(max_length=20, choices=SCRAPE_STATUS_CHOICES, default='pending')
    last_scrape_error = models.TextField(blank=True)

    #read time
    read_time_minutes = models.IntegerField(default=0)

    @staticmethod
    def normalize_url(url):
        if not url:
            return ''
        split = urlsplit(url.strip())
        scheme = split.scheme.lower() or 'https'
        netloc = split.netloc.lower()
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        path = split.path.rstrip('/') or '/'
        ignored = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'fbclid', 'gclid'}
        query_pairs = [(k, v) for k, v in parse_qsl(split.query, keep_blank_values=True) if k not in ignored]
        query = urlencode(sorted(query_pairs))
        return urlunsplit((scheme, netloc, path, query, ''))

    @staticmethod
    def _hash_text(text):
        value = (text or '').strip()
        if not value:
            return ''
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    @staticmethod
    def normalize_title(title):
        return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', (title or '').lower())).strip()

    def save(self, *args, **kwargs):
        self.title = clean_article_text(self.title, preserve_paragraphs=False)
        self.excerpt = clean_article_text(self.excerpt)
        self.content = clean_article_text(self.content)
        self.image_caption = clean_article_text(self.image_caption)
        if not self.slug and self.title:
            from django.utils.text import slugify
            self.slug = slugify(self.title)[:200]
        self.canonical_url = self.normalize_url(self.url)
        self.normalized_title_hash = self._hash_text(self.normalize_title(self.title))
        self.content_hash = self._hash_text(self.content or self.excerpt)
        if not self.source_published_id:
            self.source_published_id = self.external_id or ''
        self.scrape_status = 'complete' if self.has_full_content else self.scrape_status
        if self.word_count > 0:
            self.read_time_minutes = max(1, self.word_count // 200) #200-250 words per minute
        if self.published_time_str and not self.published_at:
            try:
                self.published_at = parser.parse(self.published_time_str, fuzzy=True)
            except:
                pass # fallback to scraped_at
        if not self.published_at:
            self.published_at = self.scraped_at or timezone.now()
            
        super().save(*args, **kwargs)

    def was_sent_to_user(self, user, days=7):
        '''Check if this article was already sent to user in the last N days'''
        from datetime import timedelta
        cutoff = timezone.now() - timedelta(days=days)
        return ArticleNotificationHistory.objects.filter(
            user=user,
            article=self,
            sent_at__gte=cutoff
        ).exists()

    def clean(self):
        if self.word_count < 0:
            raise ValidationError('Word count cannot be negative')

    def __str__(self):
        return f"{self.title[:50]}..." if len(self.title) > 50 else self.title

    class Meta:
        db_table = 'articles'
        ordering = ['-scraped_at']
        unique_together = ['external_id', 'source']
        indexes = [
            models.Index(fields=['external_id']),
            models.Index(fields=['url']),
            models.Index(fields=['canonical_url']),
            models.Index(fields=['source', 'source_published_id']),
            models.Index(fields=['content_hash']),
            models.Index(fields=['normalized_title_hash']),
            models.Index(fields=['source', '-published_at']),
            models.Index(fields=['category', '-published_at']),
            models.Index(fields=['has_full_content', '-scraped_at']),
            GinIndex(fields=['search_vector']),
            models.Index(fields=['scraped_at']),
            models.Index(fields=['published_time_str']),
        ]


class PushToken(models.Model):
    """Model to store user push notification tokens"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='push_tokens'
    )
    token = models.CharField(max_length=200, unique=True)

    # Device/platform info
    device_id = models.CharField(max_length=100, blank=True)
    platform = models.CharField(
        max_length=20,
        choices=[
            ('ios', 'iOS'),
            ('android', 'Android'),
            ('web', 'Web'),
        ],
        blank=True
    )

    # Status tracking
    is_active = models.BooleanField(default=True)
    last_used = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if not self.token.strip():
            raise ValidationError('Push token cannot be empty.')

        # Basic validation for Expo push tokens
        if self.token.startswith('ExponentPushToken[') and not self.token.endswith(']'):
            raise ValidationError('Invalid Expo push token format.')

    def __str__(self):
        return f"Push token for {self.user.username} ({self.platform or 'unknown'})"

    class Meta:
        db_table = 'push_tokens'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['token']),
            models.Index(fields=['last_used']),
        ]
        unique_together = ['user', 'device_id']  # One token per user per device

class Comment(models.Model):
    """Model for user comments on articles, supporting threaded replies."""

    # Relationships
    article = models.ForeignKey(
        'Article',  # Use string reference to avoid import cycles
        on_delete=models.CASCADE,
        related_name='comments'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='comments'
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='replies'
    )

    # Content
    content = models.TextField()

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Status
    is_approved = models.BooleanField(default=True)  # For moderation if needed

    def clean(self):
        if not self.content.strip():
            raise ValidationError('Comment content cannot be empty.')
        if self.parent and self.parent.article != self.article:
            raise ValidationError('Reply must belong to the same article as the parent comment.')

    def __str__(self):
        return f"Comment by {self.user.username} on {self.article.title[:50]}..."

    class Meta:
        db_table = 'comments'
        ordering = ['created_at']  # Oldest first; change to ['-created_at'] for newest first
        indexes = [
            models.Index(fields=['article', 'created_at']),  # For fetching comments per article
            models.Index(fields=['user', 'created_at']),     # For fetching user comments
            models.Index(fields=['parent']),                 # For reply trees
        ]

class ScheduledNotification(models.Model):
    """Model to track scheduled news update notifications"""
    
    FREQUENCY_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('custom', 'Custom'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scheduled_notifications'
    )
    frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, default='daily')
    
    # Scheduling
    scheduled_time = models.TimeField(default=timezone.now)  # When to send daily
    next_send_at = models.DateTimeField()  # Next scheduled send time
    is_active = models.BooleanField(default=True)
    
    # Content preferences
    max_articles = models.IntegerField(default=5)
    include_categories = models.ManyToManyField(Category, blank=True)
    include_sources = models.ManyToManyField(NewsSource, blank=True)
    
    # Tracking
    last_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def save(self, *args, **kwargs):
        if not self.next_send_at:
            self.calculate_next_send()
        super().save(*args, **kwargs)
    
    def calculate_next_send(self):
        """Calculate the next send time based on frequency"""
        now = timezone.now()
        
        if self.frequency == 'daily':
            # Set for same time tomorrow
            next_send = now.replace(
                hour=self.scheduled_time.hour,
                minute=self.scheduled_time.minute,
                second=0,
                microsecond=0
            ) + timezone.timedelta(days=1)
            
            # If today's time hasn't passed yet, send today
            if now.time() < self.scheduled_time:
                next_send = now.replace(
                    hour=self.scheduled_time.hour,
                    minute=self.scheduled_time.minute,
                    second=0,
                    microsecond=0
                )
            
            self.next_send_at = next_send
    
    def __str__(self):
        return f"Scheduled notifications for {self.user.username} ({self.frequency})"
    
    class Meta:
        db_table = 'scheduled_notifications'
        indexes = [
            models.Index(fields=['next_send_at', 'is_active']),
            models.Index(fields=['user', 'is_active']),
        ]


class NotificationTemplate(models.Model):
    """Templates for different types of notifications"""
    
    NOTIFICATION_TYPES = [
        ('daily_digest', 'Daily News Digest'),
        ('breaking_news', 'Breaking News'),
        ('category_update', 'Category Update'),
    ]
    
    name = models.CharField(max_length=100)
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    title_template = models.CharField(max_length=200)
    body_template = models.TextField()
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        db_table = 'notification_templates'

#Track article views
class ArticleView(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='user_article_views',
    )
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name='views')
    viewed_at = models.DateTimeField(auto_now_add=True)
    duration_seconds = models.IntegerField(default=0, blank=True)

    class Meta:
        unique_together = ['user', 'article', 'viewed_at'] #prevent duplicates

class BreakingNews(models.Model):
    """Model to track breaking news articles"""
    
    article = models.OneToOneField(
        Article,
        on_delete=models.CASCADE,
        related_name='breaking_news'
    )
    
    # Priority levels
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]
    
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    
    # Target audience filters
    target_categories = models.ManyToManyField(Category, blank=True)
    target_sources = models.ManyToManyField(NewsSource, blank=True)
    
    # Analytics
    total_recipients = models.IntegerField(default=0)
    successful_deliveries = models.IntegerField(default=0)
    failed_deliveries = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Breaking: {self.article.title[:50]}..."
    
    class Meta:
        db_table = 'breaking_news'
        verbose_name_plural = 'Breaking News'


class UserNotification(models.Model):
    """Track all notifications sent to users"""

    NOTIFICATION_TYPES = [
        ('scheduled_digest', 'Scheduled Digest'),
        ('breaking_news', 'Breaking News'),
        ('category_update', 'Category Update'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications'
    )

    # Notification content
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    title = models.CharField(max_length=200)
    body = models.TextField()

    # Related articles
    articles = models.ManyToManyField('Article', related_name='user_notifications')

    # Tracking
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    # Link to original notification objects
    scheduled_notification = models.ForeignKey(
        'ScheduledNotification',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_notifications'
    )
    breaking_news = models.ForeignKey(
        'BreakingNews',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_notifications'
    )

    # Metadata
    priority = models.CharField(max_length=10, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    def mark_as_read(self):
        """Mark notification as read"""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])

    def __str__(self):
        return f"{self.notification_type} for {self.user.username} - {self.title[:50]}"

    class Meta:
        db_table = 'user_notifications'
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-sent_at']),
            models.Index(fields=['user', '-sent_at']),
            models.Index(fields=['notification_type', '-sent_at']),
        ]


class ArticleNotificationHistory(models.Model):
    """Track which articles have been sent to which users to avoid duplicates"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='article_notification_history'
    )
    article = models.ForeignKey(
        'Article',
        on_delete=models.CASCADE,
        related_name='notification_history'
    )
    notification = models.ForeignKey(
        UserNotification,
        on_delete=models.CASCADE,
        related_name='article_history'
    )
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'article_notification_history'
        unique_together = ['user', 'article']
        indexes = [
            models.Index(fields=['user', 'article']),
            models.Index(fields=['user', '-sent_at']),
        ]

    def __str__(self):
        return f"{self.article.title[:30]} sent to {self.user.username}"


class ScrapingRun(models.Model):
    """Model to track each scraping run"""

    STATUS_CHOICES = [
        ('started', 'Started'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('partially_completed', 'Partially Completed'),
    ]

    run_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source = models.ForeignKey(NewsSource, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='started')

    # Statistics
    articles_found = models.IntegerField(default=0)
    articles_added = models.IntegerField(default=0)
    articles_updated = models.IntegerField(default=0)
    articles_skipped = models.IntegerField(default=0)

    # Timing
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    # Error tracking
    error_message = models.TextField(blank=True)
    error_count = models.IntegerField(default=0)

    # Task info
    task_id = models.CharField(max_length=100, blank=True)
    scheduled_run = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.completed_at and self.started_at:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Scraping Run {self.run_id} - {self.status} ({self.started_at})"

    class Meta:
        db_table = 'scraping_runs'
        ordering = ['-started_at']


class ScrapingLog(models.Model):
    """Detailed logs for scraping operations"""

    LOG_LEVELS = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('debug', 'Debug'),
    ]

    run = models.ForeignKey(ScrapingRun, on_delete=models.CASCADE, related_name='logs')
    level = models.CharField(max_length=10, choices=LOG_LEVELS)
    message = models.TextField()
    article_url = models.URLField(blank=True, max_length=500)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.level.upper()}: {self.message[:50]}"

    class Meta:
        db_table = 'scraping_logs'
        ordering = ['-timestamp']
