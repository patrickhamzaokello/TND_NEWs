"""
URL configuration for video endpoints
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    VideoViewSet,
    VideoUploadView,
    VideoProcessingStatusView,
    VideoRetryProcessingView,
    VideoDeleteView,
    VideoViewTrackingView,
    VideoAnalyticsView,
    BulkVideoProcessView,
    UserVideosListView,
    FeaturedVideosListView,
)

# Router for ViewSet
router = DefaultRouter()
router.register(r'videos', VideoViewSet, basename='video')

app_name = 'tndvideo'

urlpatterns = [
    # ViewSet routes (CRUD operations)
    path('', include(router.urls)),

    # Custom video actions
    path('videos/upload/', VideoUploadView.as_view(), name='video-upload'),
    path('videos/my-videos/', UserVideosListView.as_view(), name='my-videos'),
    path('videos/featured/', FeaturedVideosListView.as_view(), name='featured-videos'),
    path('videos/bulk-process/', BulkVideoProcessView.as_view(), name='bulk-process'),

    # Individual video actions
    path('videos/<uuid:id>/processing-status/', VideoProcessingStatusView.as_view(), name='video-processing-status'),
    path('videos/<uuid:id>/retry-processing/', VideoRetryProcessingView.as_view(), name='video-retry-processing'),
    path('videos/<uuid:id>/delete/', VideoDeleteView.as_view(), name='video-delete'),
    path('videos/<uuid:id>/track-view/', VideoViewTrackingView.as_view(), name='video-track-view'),
    path('videos/<uuid:id>/analytics/', VideoAnalyticsView.as_view(), name='video-analytics'),
]