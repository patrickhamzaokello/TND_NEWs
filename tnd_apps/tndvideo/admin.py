from django.contrib import admin
from .models import Video, VideoQuality, VideoProcessingQueue, VideoView, VideoComment


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'status', 'duration_seconds', 'view_count',
        'uploaded_by', 'created_at', 'is_active'
    ]
    list_filter = ['status', 'is_active', 'is_featured', 'category']
    search_fields = ['title', 'description', 'slug']
    readonly_fields = [
        'id', 'slug', 'duration_seconds', 'width', 'height',
        'view_count', 'processing_progress', 'created_at', 'updated_at'
    ]
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'title', 'slug', 'description', 'uploaded_by')
        }),
        ('Relationships', {
            'fields': ('category', 'tags')
        }),
        ('File Information', {
            'fields': ('original_file', 'original_filename', 'original_file_size')
        }),
        ('Video Metadata', {
            'fields': ('duration_seconds', 'width', 'height', 'fps', 'codec', 'bitrate')
        }),
        ('Processing', {
            'fields': (
                'status', 'processing_progress', 'processing_started_at',
                'processing_completed_at', 'processing_error'
            )
        }),
        ('Streaming', {
            'fields': ('master_playlist_path', 'metadata_file_path')
        }),
        ('Media', {
            'fields': ('thumbnail_file', 'thumbnail_url')
        }),
        ('Analytics', {
            'fields': ('view_count', 'total_watch_time_seconds')
        }),
        ('Settings', {
            'fields': ('is_active', 'is_featured', 'published_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    actions = ['mark_as_ready', 'mark_as_failed', 'reprocess_videos']

    def mark_as_ready(self, request, queryset):
        queryset.update(status='ready')

    mark_as_ready.short_description = "Mark selected videos as ready"

    def mark_as_failed(self, request, queryset):
        queryset.update(status='failed')

    mark_as_failed.short_description = "Mark selected videos as failed"

    def reprocess_videos(self, request, queryset):
        from .tasks import process_video_task
        count = 0
        for video in queryset:
            if video.status in ['failed', 'uploaded']:
                process_video_task.delay(str(video.id))
                count += 1
        self.message_user(request, f"{count} videos queued for reprocessing")

    reprocess_videos.short_description = "Reprocess selected videos"


@admin.register(VideoQuality)
class VideoQualityAdmin(admin.ModelAdmin):
    list_display = [
        'video', 'quality', 'resolution_width', 'resolution_height',
        'bitrate', 'total_segments', 'is_processed'
    ]
    list_filter = ['quality', 'is_processed']
    search_fields = ['video__title']
    readonly_fields = ['total_segments', 'total_size_bytes']


@admin.register(VideoProcessingQueue)
class VideoProcessingQueueAdmin(admin.ModelAdmin):
    list_display = [
        'video', 'status', 'priority', 'progress_percentage',
        'queued_at', 'started_at', 'retry_count'
    ]
    list_filter = ['status', 'priority']
    search_fields = ['video__title', 'task_id']
    readonly_fields = ['queued_at', 'started_at', 'completed_at', 'task_id']

    actions = ['cancel_tasks', 'retry_tasks']

    def cancel_tasks(self, request, queryset):
        queryset.update(status='cancelled')

    cancel_tasks.short_description = "Cancel selected tasks"

    def retry_tasks(self, request, queryset):
        from .tasks import process_video_task
        count = 0
        for task in queryset.filter(status='failed'):
            process_video_task.delay(str(task.video.id))
            count += 1
        self.message_user(request, f"{count} tasks queued for retry")

    retry_tasks.short_description = "Retry failed tasks"


@admin.register(VideoView)
class VideoViewAdmin(admin.ModelAdmin):
    list_display = [
        'video', 'user', 'watch_duration_seconds', 'completion_percentage',
        'device_type', 'started_at'
    ]
    list_filter = ['device_type', 'quality_watched', 'is_completed']
    search_fields = ['video__title', 'user__username', 'session_id']
    readonly_fields = ['started_at', 'updated_at', 'completion_percentage']

    def has_add_permission(self, request):
        return False


@admin.register(VideoComment)
class VideoCommentAdmin(admin.ModelAdmin):
    list_display = [
        'video', 'user', 'content_preview', 'timestamp_seconds',
        'is_approved', 'created_at'
    ]
    list_filter = ['is_approved', 'created_at']
    search_fields = ['video__title', 'user__username', 'content']
    readonly_fields = ['created_at', 'updated_at']

    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content

    content_preview.short_description = 'Content'

    actions = ['approve_comments', 'unapprove_comments']

    def approve_comments(self, request, queryset):
        queryset.update(is_approved=True)

    approve_comments.short_description = "Approve selected comments"

    def unapprove_comments(self, request, queryset):
        queryset.update(is_approved=False)

    unapprove_comments.short_description = "Unapprove selected comments"
