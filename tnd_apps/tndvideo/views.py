"""
Video upload views and helper functions
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import models
from .models import Video, VideoProcessingQueue, VideoQuality
from .tasks import process_video_task
import logging

logger = logging.getLogger(__name__)


class VideoViewSet(viewsets.ModelViewSet):
    """ViewSet for video management"""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter videos based on user permissions"""
        if self.request.user.is_staff:
            return Video.objects.all()
        return Video.objects.filter(is_active=True, status='ready')

    @action(detail=False, methods=['post'])
    def upload(self, request):
        """
        Handle video upload and queue for processing

        POST /api/videos/upload/
        {
            "title": "Video Title",
            "description": "Description",
            "category_id": 5,   # optional
            "video_file": <file>
        }
        """
        try:
            # Validate file
            video_file = request.FILES.get('video_file')
            if not video_file:
                return Response(
                    {'error': 'No video file provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Validate file size (e.g., max 2GB)
            max_size = 2 * 1024 * 1024 * 1024  # 2GB in bytes
            if video_file.size > max_size:
                return Response(
                    {'error': f'File too large. Maximum size is 2GB'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Validate file type
            allowed_types = ['video/mp4', 'video/mpeg', 'video/quicktime', 'video/x-msvideo']
            if video_file.content_type not in allowed_types:
                return Response(
                    {'error': f'Invalid file type. Allowed: MP4, MPEG, MOV, AVI'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Create video record
            video = Video.objects.create(
                title=request.data.get('title', video_file.name),
                description=request.data.get('description', ''),
                original_file=video_file,
                original_filename=video_file.name,
                uploaded_by=request.user,
                status='uploaded'
            )


            # Link to category if provided
            category_id = request.data.get('category_id')
            if category_id:
                from .models import Category
                try:
                    category = Category.objects.get(id=category_id)
                    video.category = category
                    video.save(update_fields=['category'])
                except Category.DoesNotExist:
                    pass

            # Queue for processing
            priority = request.data.get('priority', 'normal')
            queue_task = VideoProcessingQueue.objects.create(
                video=video,
                priority=priority,
                status='queued'
            )

            # Trigger processing task
            process_video_task.delay(str(video.id))

            logger.info(f"Video {video.id} uploaded and queued for processing")

            return Response({
                'id': str(video.id),
                'title': video.title,
                'status': video.status,
                'queue_position': self._get_queue_position(queue_task),
                'message': 'Video uploaded successfully and queued for processing'
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error uploading video: {str(e)}")
            return Response(
                {'error': 'Failed to upload video'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    def processing_status(self, request, pk=None):
        """
        Get video processing status

        GET /api/videos/{id}/processing_status/
        """
        video = self.get_object()

        queue_task = video.processing_tasks.filter(
            status__in=['queued', 'processing']
        ).first()

        response_data = {
            'id': str(video.id),
            'title': video.title,
            'status': video.status,
            'progress': video.processing_progress,
            'error': video.processing_error if video.status == 'failed' else None,
        }

        if queue_task:
            response_data.update({
                'queue_status': queue_task.status,
                'current_step': queue_task.current_step,
                'queue_position': self._get_queue_position(queue_task),
                'started_at': queue_task.started_at,
            })

        if video.status == 'ready':
            response_data.update({
                'master_playlist_url': request.build_absolute_uri(
                    f'/media/{video.master_playlist_path}'
                ) if video.master_playlist_path else None,
                'thumbnail_url': video.thumbnail_file.url if video.thumbnail_file else None,
                'duration': video.get_duration_formatted(),
                'qualities': list(video.qualities.values(
                    'quality', 'resolution_width', 'resolution_height', 'bitrate'
                ))
            })

        return Response(response_data)

    @action(detail=True, methods=['post'])
    def retry_processing(self, request, pk=None):
        """
        Retry failed video processing

        POST /api/videos/{id}/retry_processing/
        """
        video = self.get_object()

        if video.status != 'failed':
            return Response(
                {'error': 'Can only retry failed videos'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Reset video status
        video.status = 'uploaded'
        video.processing_error = ''
        video.processing_progress = 0
        video.save(update_fields=['status', 'processing_error', 'processing_progress'])

        # Create new queue task
        queue_task = VideoProcessingQueue.objects.create(
            video=video,
            priority='high',  # Give priority to retries
            status='queued'
        )

        # Trigger processing
        process_video_task.delay(str(video.id))

        return Response({
            'message': 'Video queued for reprocessing',
            'status': video.status
        })

    @action(detail=True, methods=['delete'])
    def delete_video(self, request, pk=None):
        """
        Delete video and all associated files

        DELETE /api/videos/{id}/delete_video/
        """
        video = self.get_object()

        # Delete processed files
        import shutil
        from pathlib import Path
        from django.conf import settings

        processed_path = Path(settings.MEDIA_ROOT) / 'videos' / 'processed' / str(video.id)
        if processed_path.exists():
            try:
                shutil.rmtree(processed_path)
                logger.info(f"Deleted processed files for video {video.id}")
            except Exception as e:
                logger.error(f"Error deleting files: {str(e)}")

        # Delete original file
        if video.original_file:
            try:
                video.original_file.delete()
            except Exception as e:
                logger.error(f"Error deleting original file: {str(e)}")

        # Delete database record
        video.delete()

        return Response(
            {'message': 'Video deleted successfully'},
            status=status.HTTP_204_NO_CONTENT
        )

    def _get_queue_position(self, queue_task):
        """Calculate position in processing queue"""
        if queue_task.status != 'queued':
            return 0

        position = VideoProcessingQueue.objects.filter(
            status='queued',
            priority__gte=queue_task.priority,
            queued_at__lt=queue_task.queued_at
        ).count() + 1

        return position


# Helper functions for video management

def get_video_stream_url(video_id, request=None):
    """
    Get the HLS streaming URL for a video

    Args:
        video_id: Video UUID
        request: Django request object (optional, for building absolute URL)

    Returns:
        str: Master playlist URL or None if not ready
    """
    try:
        video = Video.objects.get(id=video_id, status='ready')

        if not video.master_playlist_path:
            return None

        if request:
            return request.build_absolute_uri(f'/media/{video.master_playlist_path}')

        return f'/media/{video.master_playlist_path}'

    except Video.DoesNotExist:
        return None


def track_video_view(video_id, user=None, session_id=None, watch_duration=0,
                     last_position=0, quality='medium', request=None):
    """
    Track video view and update analytics

    Args:
        video_id: Video UUID
        user: User instance (optional)
        session_id: Anonymous session ID
        watch_duration: Seconds watched
        last_position: Last playback position
        quality: Quality watched
        request: Django request object (optional)
    """
    from .models import VideoView

    try:
        video = Video.objects.get(id=video_id)

        # Get or create view record
        view, created = VideoView.objects.get_or_create(
            video=video,
            user=user,
            session_id=session_id or 'anonymous',
            defaults={
                'watch_duration_seconds': watch_duration,
                'last_position_seconds': last_position,
                'quality_watched': quality,
            }
        )

        if not created:
            # Update existing view
            view.watch_duration_seconds = max(view.watch_duration_seconds, watch_duration)
            view.last_position_seconds = last_position
            view.quality_watched = quality
            view.save(update_fields=[
                'watch_duration_seconds', 'last_position_seconds',
                'quality_watched', 'updated_at'
            ])

        # Extract device info from request
        if request:
            view.ip_address = get_client_ip(request)
            view.user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]
            view.device_type = detect_device_type(request)
            view.save(update_fields=['ip_address', 'user_agent', 'device_type'])

        # Update video analytics
        video.view_count = video.video_views.count()
        video.total_watch_time_seconds = video.video_views.aggregate(
            total=models.Sum('watch_duration_seconds')
        )['total'] or 0
        video.save(update_fields=['view_count', 'total_watch_time_seconds'])

        logger.info(f"Tracked view for video {video_id}: {watch_duration}s watched")

    except Video.DoesNotExist:
        logger.warning(f"Video {video_id} not found for view tracking")
    except Exception as e:
        logger.error(f"Error tracking video view: {str(e)}")


def get_client_ip(request):
    """Extract client IP from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def detect_device_type(request):
    """Detect device type from user agent"""
    user_agent = request.META.get('HTTP_USER_AGENT', '').lower()

    if 'mobile' in user_agent or 'android' in user_agent or 'iphone' in user_agent:
        return 'mobile'
    elif 'tablet' in user_agent or 'ipad' in user_agent:
        return 'tablet'
    elif 'tv' in user_agent or 'smarttv' in user_agent:
        return 'tv'
    else:
        return 'desktop'


def get_recommended_quality(request):
    """
    Recommend video quality based on connection

    Args:
        request: Django request object

    Returns:
        str: Recommended quality ('low', 'medium', 'high')
    """
    # Simple heuristic based on device type
    device_type = detect_device_type(request)

    if device_type == 'mobile':
        return 'medium'
    elif device_type == 'desktop' or device_type == 'tv':
        return 'high'
    else:
        return 'medium'


def validate_video_file(video_file):
    """
    Validate uploaded video file

    Args:
        video_file: Django UploadedFile object

    Returns:
        tuple: (is_valid, error_message)
    """
    # Check if file exists
    if not video_file:
        return False, "No video file provided"

    # Check file size (max 2GB)
    max_size = 2 * 1024 * 1024 * 1024
    if video_file.size > max_size:
        return False, f"File too large. Maximum size is 2GB, got {video_file.size / (1024**3):.2f}GB"

    # Check minimum size (1MB)
    min_size = 1 * 1024 * 1024
    if video_file.size < min_size:
        return False, "File too small. Minimum size is 1MB"

    # Check file extension
    allowed_extensions = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.mpeg']
    file_ext = video_file.name.lower().split('.')[-1]
    if f'.{file_ext}' not in allowed_extensions:
        return False, f"Invalid file extension. Allowed: {', '.join(allowed_extensions)}"

    # Check content type
    allowed_types = [
        'video/mp4', 'video/mpeg', 'video/quicktime',
        'video/x-msvideo', 'video/x-matroska', 'video/webm'
    ]
    if video_file.content_type not in allowed_types:
        return False, f"Invalid content type: {video_file.content_type}"

    return True, None


def bulk_process_videos(video_ids, priority='normal'):
    """
    Queue multiple videos for processing

    Args:
        video_ids: List of video UUIDs
        priority: Processing priority

    Returns:
        dict: Processing results
    """
    results = {
        'queued': [],
        'failed': [],
        'already_processing': []
    }

    for video_id in video_ids:
        try:
            video = Video.objects.get(id=video_id)

            # Check if already processing
            if video.status in ['processing', 'ready']:
                results['already_processing'].append(str(video_id))
                continue

            # Queue for processing
            queue_task = VideoProcessingQueue.objects.create(
                video=video,
                priority=priority,
                status='queued'
            )

            # Trigger processing
            process_video_task.delay(str(video.id))

            results['queued'].append(str(video_id))

        except Video.DoesNotExist:
            results['failed'].append({
                'id': str(video_id),
                'error': 'Video not found'
            })
        except Exception as e:
            results['failed'].append({
                'id': str(video_id),
                'error': str(e)
            })

    return results


def get_video_analytics(video_id):
    """
    Get comprehensive analytics for a video

    Args:
        video_id: Video UUID

    Returns:
        dict: Analytics data
    """
    from django.db.models import Avg, Sum, Count
    from .models import VideoView

    try:
        video = Video.objects.get(id=video_id)

        views = VideoView.objects.filter(video=video)

        analytics = {
            'video_id': str(video.id),
            'title': video.title,
            'total_views': views.count(),
            'unique_users': views.filter(user__isnull=False).values('user').distinct().count(),
            'total_watch_time_seconds': views.aggregate(Sum('watch_duration_seconds'))['watch_duration_seconds__sum'] or 0,
            'average_watch_time_seconds': views.aggregate(Avg('watch_duration_seconds'))['watch_duration_seconds__avg'] or 0,
            'completion_rate': views.filter(is_completed=True).count() / max(views.count(), 1) * 100,
            'device_breakdown': {},
            'quality_breakdown': {},
        }

        # Device breakdown
        device_stats = views.values('device_type').annotate(count=Count('id'))
        for stat in device_stats:
            analytics['device_breakdown'][stat['device_type'] or 'unknown'] = stat['count']

        # Quality breakdown
        quality_stats = views.values('quality_watched').annotate(count=Count('id'))
        for stat in quality_stats:
            analytics['quality_breakdown'][stat['quality_watched'] or 'unknown'] = stat['count']

        return analytics

    except Video.DoesNotExist:
        return None


def cleanup_failed_uploads():
    """
    Clean up videos stuck in uploaded/pending status
    Should be run as a periodic task
    """
    from datetime import timedelta

    threshold = timezone.now() - timedelta(hours=24)

    # Find videos uploaded more than 24 hours ago but not processed
    stale_videos = Video.objects.filter(
        status__in=['pending', 'uploaded'],
        created_at__lt=threshold
    )

    cleaned_count = 0
    for video in stale_videos:
        logger.warning(f"Cleaning up stale video: {video.id}")

        # Delete original file
        if video.original_file:
            try:
                video.original_file.delete()
            except Exception as e:
                logger.error(f"Error deleting file: {str(e)}")

        # Delete video record
        video.delete()
        cleaned_count += 1

    logger.info(f"Cleaned up {cleaned_count} stale videos")
    return cleaned_count


# Serializers for API responses

from rest_framework import serializers

class VideoQualitySerializer(serializers.ModelSerializer):
    """Serializer for VideoQuality model"""

    class Meta:
        model = VideoQuality
        fields = [
            'quality', 'resolution_width', 'resolution_height',
            'bitrate', 'total_segments', 'is_processed'
        ]


class VideoSerializer(serializers.ModelSerializer):
    """Serializer for Video model"""

    qualities = VideoQualitySerializer(many=True, read_only=True)
    duration_formatted = serializers.SerializerMethodField()
    stream_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            'id', 'slug', 'title', 'description', 'status',
            'duration_seconds', 'duration_formatted', 'width', 'height',
            'view_count', 'is_featured', 'created_at', 'published_at',
            'thumbnail_url', 'stream_url', 'qualities', 'category'
        ]
        read_only_fields = ['id', 'slug', 'status', 'view_count']

    def get_duration_formatted(self, obj):
        return obj.get_duration_formatted()

    def get_stream_url(self, obj):
        if obj.status != 'ready' or not obj.master_playlist_path:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(f'/media/{obj.master_playlist_path}')
        return f'/media/{obj.master_playlist_path}'

    def get_thumbnail_url(self, obj):
        if obj.thumbnail_file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.thumbnail_file.url)
            return obj.thumbnail_file.url
        return None


class VideoUploadSerializer(serializers.Serializer):
    """Serializer for video upload"""

    title = serializers.CharField(max_length=500)
    description = serializers.CharField(required=False, allow_blank=True)
    video_file = serializers.FileField()
    category_id = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(
        choices=['low', 'normal', 'high', 'urgent'],
        default='normal'
    )

    def validate_video_file(self, value):
        is_valid, error = validate_video_file(value)
        if not is_valid:
            raise serializers.ValidationError(error)
        return value


class VideoViewTrackingSerializer(serializers.Serializer):
    """Serializer for tracking video views"""

    watch_duration_seconds = serializers.FloatField(min_value=0)
    last_position_seconds = serializers.FloatField(min_value=0)
    quality_watched = serializers.ChoiceField(
        choices=['low', 'medium', 'high'],
        default='medium'
    )
    session_id = serializers.CharField(max_length=100, required=False)