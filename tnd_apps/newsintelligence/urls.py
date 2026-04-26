# urls.py
from django.urls import path
from .views import (
    DailyDigestByDateView,
    DailyDigestDetailView,
    DailyDigestListView,
    DigestApproveView,
    DigestRejectView,
    StoryAlertListView,
    StoryClusterDetailView,
    StoryClusterListView,
    TodayDigestView,
    TrendingEntitiesView,
)

app_name = 'digests'  # optional

urlpatterns = [
    path('digests/', DailyDigestListView.as_view(), name='digest-list'),
    path('digests/today/', TodayDigestView.as_view(), name='digest-today'),
    path('digests/date/<slug:digest_date>/', DailyDigestByDateView.as_view(), name='digest-by-date'),
    path('digests/<int:pk>/approve/', DigestApproveView.as_view(), name='digest-approve'),
    path('digests/<int:pk>/reject/', DigestRejectView.as_view(), name='digest-reject'),
    path('digests/<int:pk>/', DailyDigestDetailView.as_view(), name='digest-detail'),
    path('stories/clusters/', StoryClusterListView.as_view(), name='story-cluster-list'),
    path('stories/clusters/<slug:slug>/', StoryClusterDetailView.as_view(), name='story-cluster-detail'),
    path('stories/alerts/', StoryAlertListView.as_view(), name='story-alert-list'),
    path('entities/trending/', TrendingEntitiesView.as_view(), name='trending-entities'),

    # Alternative: date-based lookup (cleaner for sharing)
    # path('digests/<date:digest_date>/', views.DailyDigestDetailView.as_view(lookup_field='digest_date'), name='digest-by-date'),
]
