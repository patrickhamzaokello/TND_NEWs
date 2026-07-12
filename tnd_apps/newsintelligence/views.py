import calendar
import base64
import json
import logging
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count, Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from tnd_apps.cache_utils import CacheKey, TTL, cached_response
from ..news_scrapping.models import Article
from .models import ArticleEnrichment, DailyDigest, Entity, EntityMention, SourcePerspective, StoryAlert, StoryCluster

logger = logging.getLogger(__name__)
from .serializers import (
    ArticleSnippetSerializer,
    DailyDigestDetailSerializer,
    DailyDigestListSerializer,
    EntityTopArticleSerializer,
    FeedInterleaveRequestSerializer,
    StoryAlertSerializer,
    StoryClusterDetailSerializer,
    StoryClusterListSerializer,
)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 60


class EntityTopArticlesPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50

    def get_page_size(self, request):
        if self.page_size_query_param not in request.query_params and 'limit' in request.query_params:
            try:
                return max(1, min(int(request.query_params['limit']), self.max_page_size))
            except (TypeError, ValueError):
                return self.page_size
        return super().get_page_size(request)


class FeedInterleavesView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = FeedInterleaveRequestSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        visible_ids = list(dict.fromkeys(data.get('visible_article_ids') or []))
        limit = data.get('limit') or 6
        offset = _decode_cursor(data.get('cursor'))
        candidates = self._build_candidates(visible_ids, data.get('surface') or 'home')
        window = candidates[offset:offset + limit]
        next_offset = offset + len(window)

        return Response({
            'results': window,
            'next_cursor': _encode_cursor(next_offset) if next_offset < len(candidates) else None,
        })

    def _build_candidates(self, visible_ids, surface):
        insert_after = visible_ids[-1] if visible_ids else None
        candidates = []
        candidates.extend(self._cluster_guides(visible_ids, insert_after))
        candidates.extend(self._alert_guides(visible_ids, insert_after))
        candidates.extend(self._entity_guides(visible_ids, insert_after))
        candidates.extend(self._context_guides(visible_ids, insert_after))

        seen = set()
        unique = []
        for item in sorted(candidates, key=lambda item: item.get('confidence', 0), reverse=True):
            if item['id'] in seen:
                continue
            seen.add(item['id'])
            item.setdefault('payload', {})
            item['payload']['surface'] = surface
            unique.append(item)
        return unique

    def _cluster_guides(self, visible_ids, insert_after):
        if not visible_ids:
            clusters = StoryCluster.objects.annotate(
                article_count=Count('cluster_articles', distinct=True),
                source_count=Count('cluster_articles__article__source', distinct=True),
            ).filter(article_count__gte=2).order_by('-importance_score', '-last_seen_at')[:8]
        else:
            clusters = StoryCluster.objects.filter(
                cluster_articles__article_id__in=visible_ids,
            ).annotate(
                article_count=Count('cluster_articles', distinct=True),
                source_count=Count('cluster_articles__article__source', distinct=True),
            ).order_by('-importance_score', '-last_seen_at')[:8]

        guides = []
        for cluster in clusters:
            source_count = getattr(cluster, 'source_count', 0)
            article_count = getattr(cluster, 'article_count', 0)
            reason = (
                f"This story is appearing across {source_count} sources and "
                f"{article_count} related articles."
            )
            if cluster.why_this_matters:
                reason = cluster.why_this_matters
            guides.append({
                'id': f'cluster-{cluster.id}',
                'insert_after_article_id': insert_after,
                'type': 'cluster',
                'label': 'NWITQ guide',
                'title': cluster.title or 'Follow this story thread',
                'reason': reason,
                'cta_label': 'Open story thread',
                'target': {'route': 'story_cluster', 'slug': cluster.slug},
                'confidence': min(0.95, 0.55 + ((cluster.importance_score or 0) / 20)),
                'expires_at': _guide_expires_at(),
                'payload': {
                    'cluster_id': cluster.id,
                    'article_count': article_count,
                    'source_count': source_count,
                },
            })
        return guides

    def _alert_guides(self, visible_ids, insert_after):
        alerts = StoryAlert.objects.select_related('cluster', 'article').filter(
            article__has_full_content=True,
        ).exclude(status='suppressed')
        if visible_ids:
            alerts = alerts.filter(Q(article_id__in=visible_ids) | Q(cluster__cluster_articles__article_id__in=visible_ids))
        alerts = alerts.order_by('-importance_score', '-created_at').distinct()[:6]

        return [
            {
                'id': f'alert-{alert.id}',
                'insert_after_article_id': insert_after or alert.article_id,
                'type': 'alert',
                'label': 'NWITQ alert',
                'title': alert.title,
                'reason': alert.reason,
                'cta_label': 'View update',
                'target': {'route': 'story_cluster', 'slug': alert.cluster.slug},
                'confidence': min(0.98, 0.6 + ((alert.importance_score or 0) / 20)),
                'expires_at': _guide_expires_at(hours=3),
                'payload': {'alert_id': alert.id, 'article_id': alert.article_id},
            }
            for alert in alerts
        ]

    def _entity_guides(self, visible_ids, insert_after):
        mentions = EntityMention.objects.filter(
            enrichment__status='completed',
            enrichment__article__has_full_content=True,
        )
        if visible_ids:
            mentions = mentions.filter(enrichment__article_id__in=visible_ids)
        else:
            mentions = mentions.filter(mention_date__gte=timezone.localdate() - timedelta(days=7))

        rows = mentions.values('normalized_name', 'entity_type').annotate(
            mention_count=Count('id'),
            article_count=Count('enrichment__article_id', distinct=True),
        ).filter(mention_count__gte=2).order_by('-mention_count')[:8]

        guides = []
        for row in rows:
            entity_name = row['normalized_name']
            guides.append({
                'id': f"entity-{row['entity_type']}-{entity_name.replace(' ', '-')}",
                'insert_after_article_id': insert_after,
                'type': 'entity',
                'label': 'Entity watch',
                'title': entity_name.title(),
                'reason': (
                    f"Mentioned {row['mention_count']} times across "
                    f"{row['article_count']} articles in this reading context."
                ),
                'cta_label': 'See entity coverage',
                'target': {
                    'route': 'entity_detail',
                    'entity': entity_name,
                    'type': row['entity_type'],
                },
                'confidence': min(0.92, 0.5 + (row['mention_count'] / 20)),
                'expires_at': _guide_expires_at(),
                'payload': row,
            })
        return guides

    def _context_guides(self, visible_ids, insert_after):
        if not visible_ids:
            return []
        enrichments = Article.objects.filter(
            id__in=visible_ids,
            has_full_content=True,
            enrichment__status='completed',
        ).select_related('enrichment').order_by('-enrichment__importance_score')[:6]

        guides = []
        for article in enrichments:
            enrichment = article.enrichment
            notes = enrichment.bias_or_framing_notes or []
            local_impact = enrichment.local_impact or {}
            reason = ''
            if notes:
                reason = str(notes[0])
            elif local_impact:
                reason = 'This story has local impact context worth checking before moving on.'
            elif enrichment.follow_up_worthy:
                reason = 'This article connects to a developing story worth following.'
            if not reason:
                continue
            guides.append({
                'id': f'context-{article.id}',
                'insert_after_article_id': article.id,
                'type': 'context',
                'label': 'NWITQ context',
                'title': 'Before you move on',
                'reason': reason,
                'cta_label': 'Open guidance',
                'target': {'route': 'article_guidance', 'article_id': article.id},
                'confidence': min(0.9, 0.5 + ((enrichment.importance_score or 0) / 20)),
                'expires_at': _guide_expires_at(),
                'payload': {'article_id': article.id},
            })
        return guides


class ArticleSummaryView(APIView):
    """
    GET /intelligence/articles/<id>/summary/

    Returns the AI summary for an article.

    Fast path  — enrichment already completed: returns immediately from DB.
    Slow path  — no enrichment yet: generates on the spot and returns the result.
    In-flight  — another worker is currently generating: returns 202 so the
                 client can retry after a short delay.
    No content — article has no full text: returns the raw excerpt with a flag.
    """
    permission_classes = [AllowAny]

    def get(self, request, article_id):
        article = Article.objects.select_related('source', 'category').filter(
            id=article_id
        ).first()
        if not article:
            raise NotFound('Article not found.')

        # ── Fast path: already enriched ───────────────────────────────────────
        try:
            enrichment = ArticleEnrichment.objects.get(article=article)
        except ArticleEnrichment.DoesNotExist:
            enrichment = None

        if enrichment and enrichment.status == 'completed':
            return Response(_summary_payload(article, enrichment, generated_now=False))

        # ── In-flight: another request/task is generating it ──────────────────
        if enrichment and enrichment.status == 'processing':
            return Response(
                {
                    'status': 'processing',
                    'message': 'Summary is being generated. Retry in a few seconds.',
                    'retry_after_seconds': 8,
                    'article_id': article_id,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        # ── No full content: return excerpt, skip generation ──────────────────
        if not article.has_full_content:
            return Response({
                'status': 'excerpt_only',
                'article_id': article_id,
                'summary': article.excerpt or '',
                'key_facts': [],
                'themes': [],
                'story_arcs': [],
                'sentiment': None,
                'sentiment_score': None,
                'importance_score': None,
                'local_impact': None,
                'bias_notes': [],
                'generated_now': False,
                'cached': False,
            })

        # ── Slow path: claim and generate ─────────────────────────────────────
        # Use update_or_create inside a transaction so concurrent requests don't
        # both try to generate — only one wins the 'processing' status update.
        with transaction.atomic():
            enrichment, created = ArticleEnrichment.objects.select_for_update().get_or_create(
                article=article,
                defaults={'status': 'processing'},
            )
            if not created:
                if enrichment.status == 'completed':
                    # Another request just finished while we were waiting for the lock
                    return Response(_summary_payload(article, enrichment, generated_now=False))
                if enrichment.status == 'processing':
                    return Response(
                        {
                            'status': 'processing',
                            'message': 'Summary is being generated. Retry in a few seconds.',
                            'retry_after_seconds': 8,
                            'article_id': article_id,
                        },
                        status=status.HTTP_202_ACCEPTED,
                    )
                # pending / failed / skipped — claim it
                enrichment.status = 'processing'
                enrichment.save(update_fields=['status'])

        # We hold the claim — generate synchronously
        try:
            from .agents import ArticleAnalysisAgent, EntityExtractionAgent
            enrichment = ArticleAnalysisAgent().process(article)
            EntityExtractionAgent().process(enrichment)
            logger.info("On-demand summary generated for article %d", article_id)
            return Response(_summary_payload(article, enrichment, generated_now=True))
        except ValueError as exc:
            # Content too short, bad LLM output, etc. — not retryable
            return Response(
                {'status': 'error', 'message': str(exc), 'article_id': article_id},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as exc:
            logger.exception("On-demand summary failed for article %d: %s", article_id, exc)
            return Response(
                {
                    'status': 'error',
                    'message': 'Summary generation failed. Please try again shortly.',
                    'article_id': article_id,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


def _summary_payload(article, enrichment: ArticleEnrichment, *, generated_now: bool) -> dict:
    """Shared response shape for completed enrichments."""
    local_impact = enrichment.local_impact or {}
    return {
        'status': 'completed',
        'article_id': article.id,
        'title': article.title,
        'source': article.source.name if article.source else None,
        'published_at': article.published_at,

        'summary': enrichment.summary,
        'key_facts': enrichment.key_facts or [],
        'themes': enrichment.themes or [],
        'story_arcs': enrichment.related_themes or [],

        'sentiment': enrichment.sentiment,
        'sentiment_score': enrichment.sentiment_score,
        'importance_score': enrichment.importance_score,

        'local_impact': {
            'regions': local_impact.get('regions', []),
            'affected_groups': local_impact.get('affected_groups', []),
            'time_horizon': local_impact.get('time_horizon'),
            'impact_note': local_impact.get('impact_note', ''),
        } if local_impact else None,

        'bias_notes': enrichment.bias_or_framing_notes or [],
        'follow_up_worthy': enrichment.follow_up_worthy,
        'controversy_flag': enrichment.controversy_flag,

        'entities': {
            'people': enrichment.entities_people or [],
            'organizations': enrichment.entities_organizations or [],
            'locations': enrichment.entities_locations or [],
        },

        'generated_now': generated_now,
        'cached': not generated_now,
        'analyzed_at': enrichment.analyzed_at,
    }


class ArticleGuidanceView(generics.GenericAPIView):
    permission_classes = [AllowAny]

    def get(self, request, article_id):
        cache_key = CacheKey.article_guidance(article_id)

        def _build():
            article = Article.objects.select_related(
                'source', 'category', 'author', 'enrichment',
            ).filter(id=article_id, has_full_content=True).first()
            if not article:
                return None

            enrichment = getattr(article, 'enrichment', None)
            cluster = StoryCluster.objects.filter(
                cluster_articles__article=article
            ).annotate(
                article_count=Count('cluster_articles', distinct=True),
                source_count=Count('cluster_articles__article__source', distinct=True),
            ).order_by('-importance_score', '-last_seen_at').first()

            key_entities = []
            if enrichment:
                key_entities = list(
                    EntityMention.objects.filter(enrichment=enrichment)
                    .values('entity_name', 'normalized_name', 'entity_type')
                    .order_by('entity_type', 'entity_name')[:12]
                )

            suggested = self._suggested_next_reads(article, cluster, enrichment)
            return {
                'article': ArticleSnippetSerializer(article).data,
                'related_cluster': self._cluster_payload(cluster),
                'key_entities': key_entities,
                'story_arcs': (enrichment.related_themes or []) if enrichment else [],
                'key_facts': (enrichment.key_facts or []) if enrichment else [],
                'why_it_matters': self._why_it_matters(article, enrichment, cluster),
                'missing_context': self._missing_context(article, enrichment, cluster),
                'suggested_next_reads': ArticleSnippetSerializer(suggested, many=True).data,
            }

        data = cached_response(cache_key, TTL.ARTICLE_GUIDANCE, _build)
        if data is None:
            raise NotFound('Article not found.')
        return Response(data)

    def _cluster_payload(self, cluster):
        if not cluster:
            return None
        return {
            'id': cluster.id,
            'title': cluster.title,
            'slug': cluster.slug,
            'summary': cluster.summary,
            'why_this_matters': cluster.why_this_matters,
            'importance_score': cluster.importance_score,
            'article_count': getattr(cluster, 'article_count', 0),
            'source_count': getattr(cluster, 'source_count', 0),
        }

    def _why_it_matters(self, article, enrichment, cluster):
        if cluster and cluster.why_this_matters:
            return cluster.why_this_matters
        if enrichment:
            impact = enrichment.local_impact or {}
            if isinstance(impact, dict) and impact.get('impact_note'):
                return impact['impact_note']
            if enrichment.summary:
                return enrichment.summary
        return article.excerpt or ''

    def _missing_context(self, article, enrichment, cluster):
        context = []
        if enrichment:
            context.extend(enrichment.bias_or_framing_notes or [])
        if cluster:
            perspectives = SourcePerspective.objects.filter(cluster=cluster).exclude(article=article)
            for perspective in perspectives[:5]:
                if perspective.omitted_context:
                    context.append({
                        'source': perspective.source.name,
                        'omitted_context': perspective.omitted_context,
                    })
        return context

    def _suggested_next_reads(self, article, cluster, enrichment):
        base_qs = Article.objects.filter(has_full_content=True).exclude(
            id=article.id
        ).select_related('source', 'category', 'author')

        # 1. Same cluster — most topically related
        if cluster:
            articles = list(base_qs.filter(
                story_cluster_links__cluster=cluster,
            ).order_by('-enrichment__importance_score', '-published_at')[:5])
            if articles:
                return articles

        if not enrichment:
            return []

        # 2. Same story arc via related_themes — articles that the enrichment
        #    agent already identified as part of the same named storyline
        arcs = enrichment.related_themes or []
        if arcs:
            articles = list(base_qs.filter(
                enrichment__status='completed',
                enrichment__related_themes__overlap=arcs,
            ).order_by('-enrichment__importance_score', '-published_at')[:5])
            if articles:
                return articles

        # 3. Shared entities — fallback when no arc signal exists
        entity_names = list(
            EntityMention.objects.filter(enrichment=enrichment)
            .values_list('normalized_name', flat=True)[:5]
        )
        if not entity_names:
            return []
        return list(
            base_qs.filter(
                enrichment__entity_mentions__normalized_name__in=entity_names,
                enrichment__status='completed',
            ).distinct().order_by('-enrichment__importance_score', '-published_at')[:5]
        )


def _encode_cursor(offset):
    payload = json.dumps({'offset': offset}).encode('utf-8')
    return base64.urlsafe_b64encode(payload).decode('ascii')


def _decode_cursor(cursor):
    if not cursor:
        return 0
    try:
        payload = base64.urlsafe_b64decode(cursor.encode('ascii'))
        return max(0, int(json.loads(payload).get('offset', 0)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0


def _guide_expires_at(hours=6):
    return (timezone.now() + timedelta(hours=hours)).isoformat()


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

    def retrieve(self, request, *args, **kwargs):
        def _build():
            instance = self.get_object()
            return self.get_serializer(instance).data
        return Response(cached_response(CacheKey.DIGEST_TODAY, TTL.DIGEST_TODAY, _build))


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

    def list(self, request, *args, **kwargs):
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', self.pagination_class.page_size))
        status_param = request.query_params.get('status', '')
        theme_param = request.query_params.get('theme', '')
        cache_key = CacheKey.cluster_list_page(page, page_size, status_param, theme_param)

        def _build():
            qs = self.filter_queryset(self.get_queryset())
            page_obj = self.paginate_queryset(qs)
            serializer = self.get_serializer(page_obj, many=True)
            return self.get_paginated_response(serializer.data).data

        return Response(cached_response(cache_key, TTL.CLUSTER_LIST, _build))


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

    def retrieve(self, request, *args, **kwargs):
        slug = kwargs.get('slug', '')
        cache_key = CacheKey.cluster_detail(slug)

        def _build():
            instance = self.get_object()
            return self.get_serializer(instance).data

        return Response(cached_response(cache_key, TTL.CLUSTER_DETAIL, _build))


class TrendingEntitiesView(generics.GenericAPIView):
    permission_classes = [AllowAny]

    def get(self, request):
        window_days = int(request.query_params.get('window_days', 7))
        cache_key = CacheKey.trending_entities(window_days)

        def _build():
            since = timezone.now().date() - timedelta(days=window_days)
            rows = EntityMention.objects.filter(
                mention_date__gte=since
            ).values('normalized_name', 'entity_name', 'entity_type').annotate(
                mention_count=Count('id')
            ).order_by('-mention_count')[:50]
            return {'window_days': window_days, 'results': list(rows)}

        return Response(cached_response(cache_key, TTL.TRENDING_ENTITIES, _build))


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

    def _ranked_articles_for_entity(self, entity_name, entity_type=None, limit=None, window_days=14):
        mentions = self._entity_mentions(entity_name, entity_type, window_days)
        seen = set()
        ranked = []

        for mention in mentions:
            article = mention.enrichment.article
            if article.id in seen:
                continue
            seen.add(article.id)
            article._entity_mention_date = mention.mention_date
            ranked.append(article)
            if limit and len(ranked) >= limit:
                break

        return ranked


class EntityTopArticlesView(EntityTopArticlesMixin, generics.GenericAPIView):
    permission_classes = [AllowAny]
    pagination_class = EntityTopArticlesPagination

    def get(self, request):
        entity = request.query_params.get('entity', '').strip()
        entity_type = request.query_params.get('type', '').strip() or None
        if not entity:
            return Response({'error': 'entity query parameter is required'}, status=400)

        try:
            window_days = int(request.query_params.get('window_days', 14))
        except (TypeError, ValueError):
            window_days = 14

        window_days = max(1, min(window_days, 90))
        articles = self._ranked_articles_for_entity(entity, entity_type, window_days=window_days)
        page = self.paginate_queryset(articles)
        serializer = EntityTopArticleSerializer(page, many=True)
        paginated = self.get_paginated_response(serializer.data).data

        return Response({
            'entity': entity,
            'type': entity_type,
            'window_days': window_days,
            **paginated,
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


# ══════════════════════════════════════════════════════════════════════════════
# Public digest homepage (replaces Swagger on the site root)
# ══════════════════════════════════════════════════════════════════════════════

def digest_home(request, digest_date=None):
    """
    Public web page: latest (or dated) daily digest on the left,
    list of previous digests on the right.
    """
    from datetime import datetime as _dt

    from django.shortcuts import render
    from django.http import Http404

    qs = DailyDigest.objects.filter(is_published=True).order_by('-digest_date')

    if digest_date:
        try:
            target = _dt.strptime(digest_date, '%Y-%m-%d').date()
        except ValueError:
            raise Http404('Invalid date')
        digest = qs.filter(digest_date=target).first()
        if not digest:
            raise Http404('No digest for this date')
    else:
        digest = qs.first()

    previous = qs.exclude(pk=digest.pk)[:14] if digest else []

    # digest_text paragraphs for clean rendering
    paragraphs = []
    if digest and digest.digest_text:
        paragraphs = [p.strip() for p in digest.digest_text.split('\n') if p.strip()]

    return render(request, 'newsintelligence/digest_home.html', {
        'digest': digest,
        'paragraphs': paragraphs,
        'previous': previous,
    })
