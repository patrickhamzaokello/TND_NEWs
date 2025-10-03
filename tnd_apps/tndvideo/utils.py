"""
Helper functions for video management
"""

import logging
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum

from .models import Video, VideoView

logger = logging.getLogger(__name__)


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
        size_gb = video_file.size / (1024 ** 3)
        return False, f"File too large. Maximum size is 2GB, got {size_gb:.2f}GB"

    # Check minimum size (1MB)
    min_size = 1 * 1024 * 1024
    if video_file.size < min_size:
        return False, "File too small. Minimum size is 1MB"

    # Check file extension
    allowed_extensions = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.mpeg']
    file_name = video_file.name.lower()
    file_ext = f".{file_name.split('.')[-1]}" if '.' in file_name else ''

    if file_ext not in allowed_extensions:
        return False, f"Invalid file extension. Allowed: {', '.join(allowed_extensions)}"

    # Check content type
    allowed_types = [
        'video/mp4', 'video/mpeg', 'video/quicktime',
        'video/x-msvideo', 'video/x-matroska', 'video/webm',
        'video/x-flv'
    ]
    if video_file.content_type and video_file.content_type not in allowed_types:
        return False, f"Invalid content type: {video_file.content_type}"

    return True, None


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
        video = Video.objects.get(id=video_id, status='ready', is_active=True)

        if not video.master_playlist_path:
            return None

        if request:
            return request.build_absolute_uri(f'/media/{video.master_playlist_path}')

        return f'/media/{video.master_playlist_path}'

    except Video.DoesNotExist:
        return None


def get_client_ip(request):
    """
    Extract client IP from request

    Args:
        request: Django request object

    Returns:
        str: Client IP address
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '')
    return ip


def detect_device_type(request):
    """
    Detect device type from user agent

    Args:
        request: Django request object

    Returns:
        str: Device type ('mobile', 'tablet', 'tv', 'desktop')
    """
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
    Recommend video quality based on device and connection

    Args:
        request: Django request object

    Returns:
        str: Recommended quality ('low', 'medium', 'high')
    """
    device_type = detect_device_type(request)

    # Mobile devices get medium quality by default
    if device_type == 'mobile':
        return 'medium'
    # Desktop and TV get high quality
    elif device_type in ['desktop', 'tv']:
        return 'high'
    # Tablets get medium
    else:
        return 'medium'


def cleanup_failed_uploads(hours=24):
    """
    Clean up videos stuck in uploaded/pending status
    Should be run as a periodic task (e.g., Celery beat)

    Args:
        hours: Number of hours after which to consider uploads stale

    Returns:
        int: Number of cleaned up videos
    """
    threshold = timezone.now() - timedelta(hours=hours)

    # Find videos uploaded more than specified hours ago but not processed
    stale_videos = Video.objects.filter(
        status__in=['pending', 'uploaded'],
        created_at__lt=threshold
    )

    cleaned_count = 0
    for video in stale_videos:
        logger.warning(f"Cleaning up stale video: {video.id} - {video.title}")

        try:
            # Delete original file
            if video.original_file:
                video.original_file.delete(save=False)

            # Delete thumbnail if exists
            if video.thumbnail_file:
                video.thumbnail_file.delete(save=False)

            # Delete video record
            video.delete()
            cleaned_count += 1

        except Exception as e:
            logger.error(f"Error cleaning up video {video.id}: {str(e)}")

    logger.info(f"Cleaned up {cleaned_count} stale videos")
    return cleaned_count


def cleanup_orphaned_files():
    """
    Clean up orphaned video files that don't have database records
    This should be run periodically and with caution

    Returns:
        int: Number of files cleaned up
    """
    from pathlib import Path
    from django.conf import settings
    import shutil

    videos_path = Path(settings.MEDIA_ROOT) / 'videos'
    if not videos_path.exists():
        return 0

    cleaned_count = 0

    # Get all video IDs from database
    existing_video_ids = set(
        str(vid) for vid in Video.objects.values_list('id', flat=True)
    )

    # Check processed videos directory
    processed_path = videos_path / 'processed'
    if processed_path.exists():
        for video_dir in processed_path.iterdir():
            if video_dir.is_dir():
                video_id = video_dir.name

                # If directory doesn't match any video in DB, delete it
                if video_id not in existing_video_ids:
                    try:
                        shutil.rmtree(video_dir)
                        logger.info(f"Deleted orphaned directory: {video_id}")
                        cleaned_count += 1
                    except Exception as e:
                        logger.error(f"Error deleting orphaned directory {video_id}: {str(e)}")

    logger.info(f"Cleaned up {cleaned_count} orphaned video directories")
    return cleaned_count


def calculate_video_completion_rate(video_id):
    """
    Calculate completion rate for a specific video

    Args:
        video_id: Video UUID

    Returns:
        float: Completion rate percentage
    """
    try:
        video = Video.objects.get(id=video_id)
        views = VideoView.objects.filter(video=video)

        total_views = views.count()
        if total_views == 0:
            return 0.0

        completed_views = views.filter(is_completed=True).count()
        return (completed_views / total_views) * 100

    except Video.DoesNotExist:
        return 0.0


def get_trending_videos(limit=10, days=7):
    """
    Get trending videos based on recent view activity

    Args:
        limit: Number of videos to return
        days: Number of days to consider for trending

    Returns:
        QuerySet: Trending videos
    """
    from django.db.models import Count

    threshold = timezone.now() - timedelta(days=days)

    trending = Video.objects.filter(
        status='ready',
        is_active=True,
        video_views__created_at__gte=threshold
    ).annotate(
        recent_views=Count('video_views')
    ).order_by('-recent_views')[:limit]

    return trending


def get_user_watch_history(user, limit=20):
    """
    Get user's video watch history

    Args:
        user: User instance
        limit: Maximum number of videos to return

    Returns:
        QuerySet: User's watched videos
    """
    if not user.is_authenticated:
        return Video.objects.none()

    return Video.objects.filter(
        video_views__user=user,
        status='ready',
        is_active=True
    ).distinct().order_by('-video_views__updated_at')[:limit]


def calculate_average_engagement_rate(video_id):
    """
    Calculate average engagement rate (% of video watched)

    Args:
        video_id: Video UUID

    Returns:
        float: Average engagement rate percentage
    """
    try:
        video = Video.objects.get(id=video_id)

        if not video.duration_seconds or video.duration_seconds == 0:
            return 0.0

        views = VideoView.objects.filter(video=video)

        if not views.exists():
            return 0.0

        total_engagement = 0
        for view in views:
            engagement = (view.watch_duration_seconds / video.duration_seconds) * 100
            total_engagement += min(engagement, 100)  # Cap at 100%

        return total_engagement / views.count()

    except Video.DoesNotExist:
        return 0.0


def get_video_quality_distribution(video_id):
    """
    Get distribution of quality preferences for a video

    Args:
        video_id: Video UUID

    Returns:
        dict: Quality distribution
    """
    from django.db.models import Count

    try:
        video = Video.objects.get(id=video_id)

        quality_dist = VideoView.objects.filter(
            video=video
        ).values('quality_watched').annotate(
            count=Count('id')
        ).order_by('-count')

        total_views = sum(item['count'] for item in quality_dist)

        distribution = {}
        for item in quality_dist:
            quality = item['quality_watched'] or 'unknown'
            count = item['count']
            percentage = (count / total_views * 100) if total_views > 0 else 0

            distribution[quality] = {
                'count': count,
                'percentage': round(percentage, 2)
            }

        return distribution

    except Video.DoesNotExist:
        return {}


def format_duration(seconds):
    """
    Format duration in seconds to human-readable string

    Args:
        seconds: Duration in seconds

    Returns:
        str: Formatted duration (e.g., "1:23:45" or "12:34")
    """
    if not seconds or seconds < 0:
        return "0:00"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def generate_video_slug(title, video_id):
    """
    Generate a unique slug for a video

    Args:
        title: Video title
        video_id: Video UUID

    Returns:
        str: Unique slug
    """
    from django.utils.text import slugify

    base_slug = slugify(title)[:50]  # Limit length
    unique_slug = f"{base_slug}-{str(video_id)[:8]}"

    return unique_slug


def estimate_processing_time(file_size_bytes, video_duration_seconds=None):
    """
    Estimate video processing time based on file size and duration

    Args:
        file_size_bytes: File size in bytes
        video_duration_seconds: Video duration (optional)

    Returns:
        int: Estimated processing time in minutes
    """
    # Very rough estimation - adjust based on your processing pipeline
    # Assume ~1GB takes about 5 minutes to process
    size_gb = file_size_bytes / (1024 ** 3)
    estimated_minutes = size_gb * 5

    # If duration is provided, factor it in
    if video_duration_seconds:
        duration_minutes = video_duration_seconds / 60
        # Longer videos take more time
        estimated_minutes += duration_minutes * 0.5

    return max(int(estimated_minutes), 1)  # At least 1 minute