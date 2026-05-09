from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DailyDigest, EntityMention, StoryAlert, StoryCluster
from .serializers import (
    DailyDigestDetailSerializer,
    DailyDigestListSerializer,
    StoryAlertSerializer,
    StoryClusterDetailSerializer,
    StoryClusterListSerializer,
)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 60


class DailyDigestListView(generics.ListAPIView):
    queryset = DailyDigest.objects.all()
    serializer_class = DailyDigestListSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = super().get_queryset().order_by('-digest_date')
        if not self.request.user.is_staff:
            qs = qs.filter(is_published=True)
        return qs


class DailyDigestDetailView(generics.RetrieveAPIView):
    queryset = DailyDigest.objects.all()
    serializer_class = DailyDigestDetailSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.is_staff:
            qs = qs.filter(is_published=True)
        return qs


class TodayDigestView(DailyDigestDetailView):
    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        obj = queryset.filter(digest_date=timezone.now().date()).first()
        if obj is None:
            raise NotFound('No published digest exists for today.')
        self.check_object_permissions(self.request, obj)
        return obj


class DailyDigestByDateView(DailyDigestDetailView):
    lookup_field = 'digest_date'
    lookup_url_kwarg = 'digest_date'


class StoryClusterListView(generics.ListAPIView):
    serializer_class = StoryClusterListSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = StoryCluster.objects.annotate(
            article_count=Count('cluster_articles', distinct=True),
            source_count=Count('cluster_articles__article__source', distinct=True),
        ).order_by('-last_seen_at', '-importance_score')

        status = self.request.query_params.get('status')
        theme = self.request.query_params.get('theme')
        if status:
            qs = qs.filter(status=status)
        if theme:
            qs = qs.filter(primary_theme=theme)
        return qs


class StoryClusterDetailView(generics.RetrieveAPIView):
    serializer_class = StoryClusterDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = 'slug'

    def get_queryset(self):
        return StoryCluster.objects.annotate(
            article_count=Count('cluster_articles', distinct=True),
            source_count=Count('cluster_articles__article__source', distinct=True),
        ).prefetch_related(
            'cluster_articles__article__source',
            'cluster_articles__article__category',
            'cluster_articles__article__author',
            'timeline_events__article__source',
            'source_perspectives__source',
            'source_perspectives__article',
        )


class TrendingEntitiesView(generics.GenericAPIView):
    permission_classes = [AllowAny]

    def get(self, request):
        window_days = int(request.query_params.get('window_days', 7))
        since = timezone.now().date() - timedelta(days=window_days)
        rows = EntityMention.objects.filter(
            mention_date__gte=since
        ).values('normalized_name', 'entity_name', 'entity_type').annotate(
            mention_count=Count('id')
        ).order_by('-mention_count')[:50]

        return Response({
            'window_days': window_days,
            'results': list(rows),
        })


class DigestApproveView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        digest = DailyDigest.objects.get(pk=pk)
        digest.editorial_review_status = 'approved'
        digest.is_published = True
        digest.reviewed_by = request.user.get_username()
        digest.reviewed_at = timezone.now()
        digest.save(update_fields=[
            'editorial_review_status', 'is_published',
            'reviewed_by', 'reviewed_at',
        ])
        return Response(DailyDigestDetailSerializer(digest).data)


class DigestRejectView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        digest = DailyDigest.objects.get(pk=pk)
        digest.editorial_review_status = 'rejected'
        digest.is_published = False
        digest.reviewed_by = request.user.get_username()
        digest.reviewed_at = timezone.now()
        digest.save(update_fields=[
            'editorial_review_status', 'is_published',
            'reviewed_by', 'reviewed_at',
        ])
        return Response(DailyDigestDetailSerializer(digest).data)


class StoryAlertListView(generics.ListAPIView):
    serializer_class = StoryAlertSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = StoryAlert.objects.select_related(
            'cluster', 'article', 'article__source', 'article__category', 'article__author'
        ).order_by('-created_at')
        status = self.request.query_params.get('status')
        if status:
            qs = qs.filter(status=status)
        else:
            qs = qs.exclude(status='suppressed')
        return qs
