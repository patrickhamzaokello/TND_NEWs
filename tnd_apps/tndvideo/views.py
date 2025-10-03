"""
Refactored video upload views using class-based views
"""

from rest_framework import viewsets, status, generics, views
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.pagination import PageNumberPagination
from django.utils import timezone
from django.db.models import Q, Sum, Avg, Count
from django.shortcuts import get_object_or_404
from pathlib import Path
from django.conf import settings
import shutil
import logging

from .models import Video, VideoProcessingQueue, VideoQuality, VideoView, Category
from .serializers import (
    VideoSerializer, VideoQualitySerializer, VideoUploadSerializer,
    VideoViewTrackingSerializer
)
from .tasks import process_video_task

logger = logging.getLogger(__name__)


# ===========================
# Pagination Classes
# ===========================

class StandardResultsSetPagination(PageNumberPagination):
    """Standard pagination for most list views"""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class LargeResultsSetPagination(PageNumberPagination):
    """Pagination for larger datasets"""
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


# ===========================
# Video ViewSet
# ===========================

class VideoViewSet(viewsets.ModelViewSet):
    """
    ViewSet for video management with full CRUD operations

    list: GET /api/videos/
    retrieve: GET /api/videos/{id}/
    create: POST /api/videos/
    update: PUT /api/videos/{id}/
    partial_update: PATCH /api/videos/{id}/
    destroy: DELETE /api/videos/{id}/
    """

    serializer_class = VideoSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    pagination_class = StandardResultsSetPagination
    lookup_field = 'id'

    def get_queryset(self):
        """Filter videos based on user permissions and query params"""
        queryset = Video.objects.select_related('category', 'uploaded_by').prefetch_related('qualities')

        # Staff can see all videos
        if self.request.user.is_staff:
            pass
        # Authenticated users see active and their own videos
        elif self.request.user.is_authenticated:
            queryset = queryset.filter(
                Q(is_active=True, status='ready') | Q(uploaded_by=self.request.user)
            )
        # Anonymous users only see ready videos
        else:
            queryset = queryset.filter(is_active=True, status='ready')

        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filter by category
        category_id = self.request.query_params.get('category')
        if category_id:
            queryset = queryset.filter(category_id=category_id)

        # Filter by featured
        is_featured = self.request.query_params.get('featured')
        if is_featured and is_featured.lower() == 'true':
            queryset = queryset.filter(is_featured=True)

        # Search by title
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )

        # Order by
        order_by = self.request.query_params.get('order_by', '-created_at')
        queryset = queryset.order_by(order_by)

        return queryset

    def get_serializer_context(self):
        """Add request to serializer context"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def perform_destroy(self, instance):
        """Soft delete video"""
        instance.is_active = False
        instance.save(update_fields=['is_active'])


# ===========================
# Video Upload View
# ===========================

class VideoUploadView(generics.CreateAPIView):
    """
    Handle video upload and queue for processing

    POST /api/videos/upload/
    Body (multipart/form-data):
    {
        "title": "Video Title",
        "description": "Description",
        "category_id": 5,
        "video_file": <file>,
        "priority": "normal"
    }
    """

    serializer_class = VideoUploadSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            video = self._create_video(serializer.validated_data)
            self._queue_for_processing(video, serializer.validated_data.get('priority', 'normal'))

            response_serializer = VideoSerializer(video, context={'request': request})

            return Response({
                'video': response_serializer.data,
                'message': 'Video uploaded successfully and queued for processing',
                'queue_position': self._get_queue_position(video)
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error uploading video: {str(e)}", exc_info=True)
            return Response(
                {'error': 'Failed to upload video', 'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _create_video(self, validated_data):
        """Create video instance"""
        video_file = validated_data['video_file']

        video = Video.objects.create(
            title=validated_data['title'],
            description=validated_data.get('description', ''),
            original_file=video_file,
            original_filename=video_file.name,
            uploaded_by=self.request.user,
            status='uploaded'
        )

        # Link category if provided
        category_id = validated_data.get('category_id')
        if category_id:
            try:
                category = Category.objects.get(id=category_id)
                video.category = category
                video.save(update_fields=['category'])
            except Category.DoesNotExist:
                logger.warning(f"Category {category_id} not found")

        return video

    def _queue_for_processing(self, video, priority):
        """Queue video for processing"""
        queue_task = VideoProcessingQueue.objects.create(
            video=video,
            priority=priority,
            status='queued'
        )

        # Trigger async processing task
        process_video_task.delay(str(video.id))

        logger.info(f"Video {video.id} queued for processing with priority {priority}")
        return queue_task

    def _get_queue_position(self, video):
        """Get position in processing queue"""
        queue_task = video.processing_tasks.filter(status='queued').first()
        if not queue_task:
            return 0

        position = VideoProcessingQueue.objects.filter(
            status='queued',
            priority__gte=queue_task.priority,
            queued_at__lt=queue_task.queued_at
        ).count() + 1

        return position


# ===========================
# Video Processing Status View
# ===========================

class VideoProcessingStatusView(generics.RetrieveAPIView):
    """
    Get video processing status

    GET /api/videos/{id}/processing-status/
    """

    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Video.objects.all()

    def retrieve(self, request, *args, **kwargs):
        video = self.get_object()

        # Check permissions - users can only see their own videos unless staff
        if not request.user.is_staff and video.uploaded_by != request.user:
            return Response(
                {'error': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )

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
                'queue_position': self._calculate_queue_position(queue_task),
                'started_at': queue_task.started_at,
                'estimated_completion': self._estimate_completion(queue_task),
            })

        if video.status == 'ready':
            response_data.update({
                'master_playlist_url': request.build_absolute_uri(
                    f'/media/{video.master_playlist_path}'
                ) if video.master_playlist_path else None,
                'thumbnail_url': video.thumbnail_file.url if video.thumbnail_file else None,
                'duration': video.get_duration_formatted(),
                'qualities': VideoQualitySerializer(video.qualities.all(), many=True).data
            })

        return Response(response_data)

    def _calculate_queue_position(self, queue_task):
        """Calculate position in queue"""
        if queue_task.status != 'queued':
            return 0

        return VideoProcessingQueue.objects.filter(
            status='queued',
            priority__gte=queue_task.priority,
            queued_at__lt=queue_task.queued_at
        ).count() + 1

    def _estimate_completion(self, queue_task):
        """Estimate completion time (simplified)"""
        # This is a placeholder - implement based on your processing metrics
        if queue_task.status == 'processing':
            return "Processing now"
        elif queue_task.status == 'queued':
            position = self._calculate_queue_position(queue_task)
            return f"~{position * 5} minutes"
        return None


# ===========================
# Video Retry Processing View
# ===========================

class VideoRetryProcessingView(generics.GenericAPIView):
    """
    Retry failed video processing

    POST /api/videos/{id}/retry-processing/
    """

    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Video.objects.all()

    def post(self, request, *args, **kwargs):
        video = self.get_object()

        # Check permissions
        if not request.user.is_staff and video.uploaded_by != request.user:
            return Response(
                {'error': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )

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

        # Create new queue task with high priority
        queue_task = VideoProcessingQueue.objects.create(
            video=video,
            priority='high',
            status='queued'
        )

        # Trigger processing
        process_video_task.delay(str(video.id))

        logger.info(f"Video {video.id} queued for reprocessing")

        return Response({
            'message': 'Video queued for reprocessing',
            'status': video.status,
            'queue_position': self._calculate_queue_position(queue_task)
        })

    def _calculate_queue_position(self, queue_task):
        """Calculate queue position"""
        return VideoProcessingQueue.objects.filter(
            status='queued',
            priority__gte=queue_task.priority,
            queued_at__lt=queue_task.queued_at
        ).count() + 1


# ===========================
# Video Delete View
# ===========================

class VideoDeleteView(generics.DestroyAPIView):
    """
    Delete video and all associated files

    DELETE /api/videos/{id}/delete/
    """

    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Video.objects.all()

    def perform_destroy(self, instance):
        """Delete video and associated files"""
        # Check permissions
        if not self.request.user.is_staff and instance.uploaded_by != self.request.user:
            raise PermissionError("Permission denied")

        # Delete processed files
        processed_path = Path(settings.MEDIA_ROOT) / 'videos' / 'processed' / str(instance.id)
        if processed_path.exists():
            try:
                shutil.rmtree(processed_path)
                logger.info(f"Deleted processed files for video {instance.id}")
            except Exception as e:
                logger.error(f"Error deleting processed files: {str(e)}")

        # Delete original file
        if instance.original_file:
            try:
                instance.original_file.delete(save=False)
            except Exception as e:
                logger.error(f"Error deleting original file: {str(e)}")

        # Delete thumbnail
        if instance.thumbnail_file:
            try:
                instance.thumbnail_file.delete(save=False)
            except Exception as e:
                logger.error(f"Error deleting thumbnail: {str(e)}")

        # Delete database record
        instance.delete()
        logger.info(f"Video {instance.id} deleted successfully")

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            self.perform_destroy(instance)
            return Response(
                {'message': 'Video deleted successfully'},
                status=status.HTTP_204_NO_CONTENT
            )
        except PermissionError:
            return Response(
                {'error': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )
        except Exception as e:
            logger.error(f"Error deleting video: {str(e)}", exc_info=True)
            return Response(
                {'error': 'Failed to delete video'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ===========================
# Video View Tracking
# ===========================

class VideoViewTrackingView(generics.CreateAPIView):
    """
    Track video views and watch analytics

    POST /api/videos/{id}/track-view/
    Body:
    {
        "watch_duration_seconds": 120.5,
        "last_position_seconds": 150.0,
        "quality_watched": "medium",
        "session_id": "anonymous-session-id"
    }
    """

    serializer_class = VideoViewTrackingSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    lookup_field = 'id'

    def create(self, request, *args, **kwargs):
        video_id = kwargs.get('id')
        video = get_object_or_404(Video, id=video_id, status='ready')

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user if request.user.is_authenticated else None
        session_id = serializer.validated_data.get('session_id', 'anonymous')

        view_record = self._track_view(
            video=video,
            user=user,
            session_id=session_id,
            validated_data=serializer.validated_data
        )

        return Response({
            'message': 'View tracked successfully',
            'total_views': video.view_count,
            'watch_duration': view_record.watch_duration_seconds
        }, status=status.HTTP_201_CREATED)

    def _track_view(self, video, user, session_id, validated_data):
        """Track video view"""
        watch_duration = validated_data['watch_duration_seconds']
        last_position = validated_data['last_position_seconds']
        quality = validated_data['quality_watched']

        # Get or create view record
        view, created = VideoView.objects.get_or_create(
            video=video,
            user=user,
            session_id=session_id,
            defaults={
                'watch_duration_seconds': watch_duration,
                'last_position_seconds': last_position,
                'quality_watched': quality,
                'ip_address': self._get_client_ip(),
                'user_agent': self.request.META.get('HTTP_USER_AGENT', '')[:500],
                'device_type': self._detect_device_type(),
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

        # Update video analytics
        self._update_video_analytics(video)

        logger.info(f"Tracked view for video {video.id}: {watch_duration}s watched")

        return view

    def _update_video_analytics(self, video):
        """Update video-level analytics"""
        video.view_count = video.video_views.count()
        video.total_watch_time_seconds = video.video_views.aggregate(
            total=Sum('watch_duration_seconds')
        )['total'] or 0
        video.save(update_fields=['view_count', 'total_watch_time_seconds'])

    def _get_client_ip(self):
        """Extract client IP from request"""
        x_forwarded_for = self.request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return self.request.META.get('REMOTE_ADDR', '')

    def _detect_device_type(self):
        """Detect device type from user agent"""
        user_agent = self.request.META.get('HTTP_USER_AGENT', '').lower()

        if 'mobile' in user_agent or 'android' in user_agent or 'iphone' in user_agent:
            return 'mobile'
        elif 'tablet' in user_agent or 'ipad' in user_agent:
            return 'tablet'
        elif 'tv' in user_agent or 'smarttv' in user_agent:
            return 'tv'
        return 'desktop'


# ===========================
# Video Analytics View
# ===========================

class VideoAnalyticsView(generics.RetrieveAPIView):
    """
    Get comprehensive analytics for a video

    GET /api/videos/{id}/analytics/
    """

    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Video.objects.all()

    def retrieve(self, request, *args, **kwargs):
        video = self.get_object()

        # Check permissions - only owner or staff can see analytics
        if not request.user.is_staff and video.uploaded_by != request.user:
            return Response(
                {'error': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )

        analytics = self._calculate_analytics(video)

        return Response(analytics)

    def _calculate_analytics(self, video):
        """Calculate comprehensive analytics"""
        views = VideoView.objects.filter(video=video)

        # Basic stats
        total_views = views.count()
        unique_users = views.filter(user__isnull=False).values('user').distinct().count()

        # Watch time stats
        watch_stats = views.aggregate(
            total_watch_time=Sum('watch_duration_seconds'),
            avg_watch_time=Avg('watch_duration_seconds')
        )

        # Completion rate
        completed_views = views.filter(is_completed=True).count()
        completion_rate = (completed_views / total_views * 100) if total_views > 0 else 0

        # Device breakdown
        device_breakdown = {}
        device_stats = views.values('device_type').annotate(count=Count('id'))
        for stat in device_stats:
            device_breakdown[stat['device_type'] or 'unknown'] = stat['count']

        # Quality breakdown
        quality_breakdown = {}
        quality_stats = views.values('quality_watched').annotate(count=Count('id'))
        for stat in quality_stats:
            quality_breakdown[stat['quality_watched'] or 'unknown'] = stat['count']

        # Engagement rate (views that watched >25%)
        if video.duration_seconds:
            engaged_views = views.filter(
                watch_duration_seconds__gte=video.duration_seconds * 0.25
            ).count()
            engagement_rate = (engaged_views / total_views * 100) if total_views > 0 else 0
        else:
            engagement_rate = 0

        return {
            'video_id': str(video.id),
            'title': video.title,
            'status': video.status,
            'duration_seconds': video.duration_seconds,
            'total_views': total_views,
            'unique_users': unique_users,
            'total_watch_time_seconds': watch_stats['total_watch_time'] or 0,
            'average_watch_time_seconds': round(watch_stats['avg_watch_time'] or 0, 2),
            'completion_rate': round(completion_rate, 2),
            'engagement_rate': round(engagement_rate, 2),
            'device_breakdown': device_breakdown,
            'quality_breakdown': quality_breakdown,
        }


# ===========================
# Bulk Video Operations View
# ===========================

class BulkVideoProcessView(views.APIView):
    """
    Queue multiple videos for processing

    POST /api/videos/bulk-process/
    Body:
    {
        "video_ids": ["uuid1", "uuid2", "uuid3"],
        "priority": "high"
    }
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        video_ids = request.data.get('video_ids', [])
        priority = request.data.get('priority', 'normal')

        if not video_ids:
            return Response(
                {'error': 'No video IDs provided'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(video_ids, list):
            return Response(
                {'error': 'video_ids must be a list'},
                status=status.HTTP_400_BAD_REQUEST
            )

        results = self._bulk_process(video_ids, priority)

        return Response(results, status=status.HTTP_200_OK)

    def _bulk_process(self, video_ids, priority):
        """Process multiple videos"""
        results = {
            'queued': [],
            'failed': [],
            'already_processing': []
        }

        for video_id in video_ids:
            try:
                video = Video.objects.get(id=video_id)

                # Check permissions
                if not self.request.user.is_staff and video.uploaded_by != self.request.user:
                    results['failed'].append({
                        'id': str(video_id),
                        'error': 'Permission denied'
                    })
                    continue

                # Check if already processing
                if video.status in ['processing', 'ready']:
                    results['already_processing'].append(str(video_id))
                    continue

                # Queue for processing
                VideoProcessingQueue.objects.create(
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


# ===========================
# User's Videos List View
# ===========================

class UserVideosListView(generics.ListAPIView):
    """
    List videos uploaded by the authenticated user

    GET /api/videos/my-videos/
    """

    serializer_class = VideoSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return Video.objects.filter(
            uploaded_by=self.request.user
        ).select_related('category').prefetch_related('qualities').order_by('-created_at')


# ===========================
# Featured Videos List View
# ===========================

class FeaturedVideosListView(generics.ListAPIView):
    """
    List featured videos

    GET /api/videos/featured/
    """

    serializer_class = VideoSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return Video.objects.filter(
            is_featured=True,
            is_active=True,
            status='ready'
        ).select_related('category').prefetch_related('qualities').order_by('-published_at')