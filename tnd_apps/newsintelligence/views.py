import calendar
from datetime import date, timedelta

from django.db.models import Count, Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DailyDigest, Entity, EntityMention, StoryAlert, StoryCluster
from .serializers import (
    DailyDigestDetailSerializer,
    DailyDigestListSerializer,
    EntityTopArticleSerializer,
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
            article_count=Count(
                'cluster_articles',
                filter=Q(cluster_articles__article__has_full_content=True),
                distinct=True,
            ),
            source_count=Count(
                'cluster_articles__article__source',
                filter=Q(cluster_articles__article__has_full_content=True),
                distinct=True,
            ),
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
            article_count=Count(
                'cluster_articles',
                filter=Q(cluster_articles__article__has_full_content=True),
                distinct=True,
            ),
            source_count=Count(
                'cluster_articles__article__source',
                filter=Q(cluster_articles__article__has_full_content=True),
                distinct=True,
            ),
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


class EntityMentionCalendarView(generics.GenericAPIView):
    permission_classes = [AllowAny]

    def get(self, request):
        entity = request.query_params.get('entity', '').strip()
        entity_type = request.query_params.get('type', '').strip() or None
        month_param = request.query_params.get('month', '').strip()

        if not entity:
            return Response({'error': 'entity query parameter is required'}, status=400)

        try:
            start_date = self._parse_month(month_param)
        except ValueError:
            return Response({'error': 'month must use YYYY-MM format'}, status=400)

        days_in_month = calendar.monthrange(start_date.year, start_date.month)[1]
        end_date = date(start_date.year, start_date.month, days_in_month) + timedelta(days=1)
        normalized_names = self._normalized_names(entity, entity_type)

        rows = EntityMention.objects.filter(
            mention_date__gte=start_date,
            mention_date__lt=end_date,
            enrichment__status='completed',
            enrichment__article__has_full_content=True,
            enrichment__article__source__is_active=True,
        ).filter(
            Q(normalized_name__in=normalized_names) | Q(entity_name__iexact=entity)
        )
        if entity_type:
            rows = rows.filter(entity_type=entity_type)

        rows = rows.values('mention_date').annotate(
            mention_count=Count('id'),
            article_count=Count('enrichment__article_id', distinct=True),
        ).order_by('mention_date')

        counts_by_date = {
            row['mention_date']: {
                'mention_count': row['mention_count'],
                'article_count': row['article_count'],
            }
            for row in rows
        }
        max_count = max((item['mention_count'] for item in counts_by_date.values()), default=0)

        days = []
        for day in range(1, days_in_month + 1):
            current = date(start_date.year, start_date.month, day)
            counts = counts_by_date.get(current, {'mention_count': 0, 'article_count': 0})
            mention_count = counts['mention_count']
            days.append({
                'date': current.isoformat(),
                'day': day,
                'weekday': current.weekday(),
                'mention_count': mention_count,
                'article_count': counts['article_count'],
                'level': self._intensity_level(mention_count, max_count),
            })

        return Response({
            'entity': entity,
            'normalized_names': normalized_names,
            'type': entity_type,
            'month': start_date.strftime('%Y-%m'),
            'start_date': start_date.isoformat(),
            'end_date': (end_date - timedelta(days=1)).isoformat(),
            'max_count': max_count,
            'total_mentions': sum(day['mention_count'] for day in days),
            'total_articles': sum(day['article_count'] for day in days),
            'days': days,
        })

    def _parse_month(self, month_param):
        if not month_param:
            today = timezone.localdate()
            return date(today.year, today.month, 1)
        year_text, month_text = month_param.split('-', 1)
        year = int(year_text)
        month = int(month_text)
        if month < 1 or month > 12:
            raise ValueError
        return date(year, month, 1)

    def _normalized_names(self, entity_name, entity_type=None):
        normalized_name = entity_name.lower().strip()
        names = {normalized_name}
        entities = Entity.objects.filter(
            Q(normalized_name=normalized_name) | Q(name__iexact=entity_name)
        )
        if entity_type:
            entities = entities.filter(entity_type=entity_type)
        for entity in entities[:5]:
            names.add(entity.normalized_name.lower().strip())
            for alias in entity.aliases or []:
                alias_name = str(alias).lower().strip()
                if alias_name:
                    names.add(alias_name)
        return sorted(names)

    def _intensity_level(self, count, max_count):
        if count <= 0 or max_count <= 0:
            return 0
        ratio = count / max_count
        if ratio <= 0.25:
            return 1
        if ratio <= 0.5:
            return 2
        if ratio <= 0.75:
            return 3
        return 4


class EntityTopArticlesMixin:
    def _entity_mentions(self, entity_name, entity_type=None, window_days=14):
        since = timezone.now().date() - timedelta(days=window_days)
        normalized_name = (entity_name or '').strip().lower()
        queryset = EntityMention.objects.filter(
            mention_date__gte=since,
            enrichment__status='completed',
            enrichment__article__has_full_content=True,
            enrichment__article__source__is_active=True,
        ).filter(
            Q(normalized_name=normalized_name) | Q(entity_name__iexact=(entity_name or '').strip())
        ).select_related(
            'enrichment',
            'enrichment__article',
            'enrichment__article__source',
        ).annotate(
            view_count=Count('enrichment__article__views', distinct=True)
        )
        if entity_type:
            queryset = queryset.filter(entity_type=entity_type)
        return queryset.order_by(
            '-enrichment__importance_score',
            '-view_count',
            '-mention_date',
            '-enrichment__article__scraped_at',
        )

    def _ranked_articles_for_entity(self, entity_name, entity_type=None, limit=5, window_days=14):
        mentions = self._entity_mentions(entity_name, entity_type, window_days)
        seen = set()
        ranked = []

        for mention in mentions[:limit * 4]:
            article = mention.enrichment.article
            if article.id in seen:
                continue
            seen.add(article.id)
            ranked.append(article)
            if len(ranked) >= limit:
                break

        return ranked


class EntityTopArticlesView(EntityTopArticlesMixin, generics.GenericAPIView):
    permission_classes = [AllowAny]

    def get(self, request):
        entity = request.query_params.get('entity', '').strip()
        entity_type = request.query_params.get('type', '').strip() or None
        if not entity:
            return Response({'error': 'entity query parameter is required'}, status=400)

        try:
            limit = int(request.query_params.get('limit', 5))
            window_days = int(request.query_params.get('window_days', 14))
        except (TypeError, ValueError):
            limit = 5
            window_days = 14

        limit = max(1, min(limit, 20))
        window_days = max(1, min(window_days, 90))
        articles = self._ranked_articles_for_entity(entity, entity_type, limit, window_days)

        return Response({
            'entity': entity,
            'type': entity_type,
            'window_days': window_days,
            'results': EntityTopArticleSerializer(articles, many=True).data,
        })


class TopEntitiesWithArticlesView(EntityTopArticlesMixin, generics.GenericAPIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            entity_limit = int(request.query_params.get('entity_limit', 10))
            articles_per_entity = int(request.query_params.get('articles_per_entity', 3))
            window_days = int(request.query_params.get('window_days', 7))
        except (TypeError, ValueError):
            entity_limit = 10
            articles_per_entity = 3
            window_days = 7

        entity_limit = max(1, min(entity_limit, 30))
        articles_per_entity = max(1, min(articles_per_entity, 10))
        window_days = max(1, min(window_days, 90))
        since = timezone.now().date() - timedelta(days=window_days)

        entities = EntityMention.objects.filter(
            mention_date__gte=since,
            enrichment__status='completed',
            enrichment__article__has_full_content=True,
            enrichment__article__source__is_active=True,
        ).annotate(
            resolved_name=Coalesce(NullIf('normalized_name', Value('')), 'entity_name'),
        ).values(
            'resolved_name',
            'entity_type',
        ).annotate(
            mention_count=Count('id'),
        ).order_by('-mention_count')[:entity_limit]

        results = []
        for entity in entities:
            articles = self._ranked_articles_for_entity(
                entity['resolved_name'],
                entity['entity_type'],
                articles_per_entity,
                window_days,
            )
            if not articles:
                continue
            results.append({
                'entity': entity['resolved_name'].title(),
                'normalized_name': entity['resolved_name'],
                'type': entity['entity_type'],
                'mention_count': entity['mention_count'],
                'articles': EntityTopArticleSerializer(articles, many=True).data,
            })

        return Response({
            'window_days': window_days,
            'results': results,
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
        ).filter(article__has_full_content=True).order_by('-created_at')
        status = self.request.query_params.get('status')
        if status:
            qs = qs.filter(status=status)
        else:
            qs = qs.exclude(status='suppressed')
        return qs
