from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid
import os

from tnd_apps.news_scrapping.models import Category, Tag


class Video(models.Model):
    """Main model for video content"""

    VIDEO_STATUS = [
        ('pending', 'Pending Upload'),
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('ready', 'Ready'),
        ('failed', 'Failed'),
        ('archived', 'Archived'),
    ]

    # Unique identifiers
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=200, unique=True)

    # Basic info
    title = models.CharField(max_length=500)
    summary = models.TextField(blank=True)
    description = models.TextField(blank=True)


    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_videos'
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    tags = models.ManyToManyField(Tag, blank=True)

    # Original file
    original_file = models.FileField(upload_to='videos/originals/%Y/%m/%d/', max_length=500)
    original_file_size = models.BigIntegerField(default=0, help_text='Size in bytes')
    original_filename = models.CharField(max_length=255, blank=True)

    # Video metadata
    duration_seconds = models.FloatField(null=True, blank=True)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    fps = models.FloatField(null=True, blank=True, help_text='Frames per second')
    bitrate = models.IntegerField(null=True, blank=True, help_text='Bitrate in kbps')
    codec = models.CharField(max_length=50, blank=True)

    # Thumbnail
    thumbnail_url = models.URLField(blank=True, max_length=500)
    thumbnail_file = models.ImageField(
        upload_to='videos/thumbnails/%Y/%m/%d/',
        blank=True,
        null=True
    )

    # HLS streaming paths
    master_playlist_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='Path to master.m3u8 file'
    )
    metadata_file_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='Path to metadata JSON file'
    )

    # Processing status
    status = models.CharField(max_length=20, choices=VIDEO_STATUS, default='pending')
    processing_progress = models.IntegerField(
        default=0,
        help_text='Processing progress percentage (0-100)'
    )
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)

    # Analytics
    view_count = models.IntegerField(default=0)
    total_watch_time_seconds = models.BigIntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    # Settings
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.slug and self.title:
            from django.utils.text import slugify
            self.slug = slugify(self.title)[:200]
        super().save(*args, **kwargs)

    def get_duration_formatted(self):
        """Return duration in HH:MM:SS format"""
        if not self.duration_seconds:
            return "00:00"

        hours = int(self.duration_seconds // 3600)
        minutes = int((self.duration_seconds % 3600) // 60)
        seconds = int(self.duration_seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'videos'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'is_active']),
            models.Index(fields=['created_at']),
            models.Index(fields=['published_at']),
        ]


class VideoQuality(models.Model):
    """Model for different quality variants of a video (HLS streaming)"""

    QUALITY_CHOICES = [
        ('low', 'Low - 360p'),
        ('medium', 'Medium - 720p'),
        ('high', 'High - 1080p'),
    ]

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name='qualities'
    )
    quality = models.CharField(max_length=10, choices=QUALITY_CHOICES)

    # Technical specs
    resolution_width = models.IntegerField(help_text='Width in pixels')
    resolution_height = models.IntegerField(help_text='Height in pixels')
    bitrate = models.IntegerField(help_text='Target bitrate in kbps')

    # HLS specific
    playlist_file_path = models.CharField(
        max_length=500,
        help_text='Path to quality-specific playlist file (e.g., low.m3u8)'
    )
    segment_duration = models.FloatField(
        default=4.0,
        help_text='Duration of each segment in seconds'
    )
    total_segments = models.IntegerField(default=0)

    # File size and storage
    total_size_bytes = models.BigIntegerField(default=0)

    # Processing status
    is_processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.video.title} - {self.get_quality_display()}"

    class Meta:
        db_table = 'video_qualities'
        unique_together = ['video', 'quality']
        ordering = ['video', 'quality']
        indexes = [
            models.Index(fields=['video', 'quality']),
            models.Index(fields=['is_processed']),
        ]



class VideoProcessingQueue(models.Model):
    """Queue for video processing tasks"""

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name='processing_tasks'
    )

    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='normal')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')

    # Task details
    task_id = models.CharField(max_length=100, blank=True, help_text='Celery/RQ task ID')
    worker_id = models.CharField(max_length=100, blank=True)

    # Progress tracking
    current_step = models.CharField(
        max_length=100,
        blank=True,
        help_text='Current processing step (e.g., "Generating 720p")'
    )
    progress_percentage = models.IntegerField(default=0)

    # Timing
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Error handling
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)

    # Metadata
    processing_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text='Additional metadata about processing'
    )

    def save(self, *args, **kwargs):
        if self.status == 'processing' and not self.started_at:
            self.started_at = timezone.now()
        if self.status in ['completed', 'failed', 'cancelled'] and not self.completed_at:
            self.completed_at = timezone.now()
        super().save(*args, **kwargs)

    def get_duration(self):
        """Calculate processing duration"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def __str__(self):
        return f"Processing task for {self.video.title} - {self.status}"

    class Meta:
        db_table = 'video_processing_queue'
        ordering = ['-priority', 'queued_at']
        indexes = [
            models.Index(fields=['status', 'priority']),
            models.Index(fields=['video']),
            models.Index(fields=['queued_at']),
        ]


class VideoView(models.Model):
    """Track video views and watch time"""

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name='video_views'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='video_views'
    )

    # Session info
    session_id = models.CharField(
        max_length=100,
        help_text='Anonymous session tracking'
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    # Watch metrics
    watch_duration_seconds = models.FloatField(
        default=0,
        help_text='How long the user watched'
    )
    completion_percentage = models.FloatField(
        default=0,
        help_text='Percentage of video watched'
    )
    quality_watched = models.CharField(
        max_length=10,
        blank=True,
        help_text='Quality level watched most'
    )

    # Viewing details
    started_at = models.DateTimeField(auto_now_add=True)
    last_position_seconds = models.FloatField(
        default=0,
        help_text='Last playback position'
    )
    is_completed = models.BooleanField(
        default=False,
        help_text='Watched more than 90%'
    )

    # Device info
    device_type = models.CharField(
        max_length=20,
        blank=True,
        choices=[
            ('mobile', 'Mobile'),
            ('tablet', 'Tablet'),
            ('desktop', 'Desktop'),
            ('tv', 'Smart TV'),
        ]
    )

    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.video.duration_seconds and self.watch_duration_seconds:
            self.completion_percentage = min(
                100.0,
                (self.watch_duration_seconds / self.video.duration_seconds) * 100
            )
            self.is_completed = self.completion_percentage >= 90.0
        super().save(*args, **kwargs)

    def __str__(self):
        user_str = self.user.username if self.user else f"Anonymous ({self.session_id[:8]})"
        return f"{user_str} viewed {self.video.title}"

    class Meta:
        db_table = 'video_views'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['video', 'started_at']),
            models.Index(fields=['user', 'started_at']),
            models.Index(fields=['session_id']),
        ]


class VideoComment(models.Model):
    """Comments on videos with timestamp support"""

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name='comments'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='video_comments'
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
    timestamp_seconds = models.FloatField(
        null=True,
        blank=True,
        help_text='Timestamp in video where comment was made'
    )

    # Moderation
    is_approved = models.BooleanField(default=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if not self.content.strip():
            raise ValidationError('Comment content cannot be empty.')
        if self.parent and self.parent.video != self.video:
            raise ValidationError('Reply must belong to the same video.')
        if self.timestamp_seconds and self.video.duration_seconds:
            if self.timestamp_seconds > self.video.duration_seconds:
                raise ValidationError('Timestamp cannot exceed video duration.')

    def __str__(self):
        return f"Comment by {self.user.username} on {self.video.title}"

    class Meta:
        db_table = 'video_comments'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['video', 'created_at']),
            models.Index(fields=['user']),
            models.Index(fields=['parent']),
        ]