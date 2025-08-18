from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
import uuid


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
    scraped_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Status
    is_processed = models.BooleanField(default=False)
    has_full_content = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.slug and self.title:
            from django.utils.text import slugify
            self.slug = slugify(self.title)[:200]
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