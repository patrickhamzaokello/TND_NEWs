from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
import uuid
from dateutil import parser
from django.conf import settings


class NewsSource(models.Model):
    """Model to track different news sources"""
    name = models.CharField(max_length=100, unique=True)
    base_url = models.URLField()
    news_url = models.URLField()
    is_active = models.BooleanField(default=True)
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

    # Unique identifier and basic info
    external_id = models.CharField(max_length=50, db_index=True)  # post-46006
    url = models.URLField(unique=True, max_length=500)
    title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=200, blank=True)

    # Content
    excerpt = models.TextField(blank=True)
    content = models.TextField(blank=True)
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

    #read time
    read_time_minutes = models.IntegerField(default=0)

    def save(self, *args, **kwargs):
        if not self.slug and self.title:
            from django.utils.text import slugify
            self.slug = slugify(self.title)[:200]
        if self.word_count > 0:
            self.read_time_minutes = max(1, self.word_count // 200) #200-250 words per minute
        if self.published_time_str and not self.published_at:
            try:
                self.published_at = parser.parse(self.published_time_str, fuzzy=True)
            except:
                pass # fallback to scraped_at
        if not self.published_at:
            self.published_at = self.scraped_at
            
        super().save(*args, **kwargs)

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