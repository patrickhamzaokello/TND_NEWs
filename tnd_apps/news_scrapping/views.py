# Create your views here.
from rest_framework import serializers, viewsets, status, generics, status, views
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Q, F, Case, When, IntegerField, FloatField, Value
from django.db.models.functions import Greatest
from datetime import timedelta
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from .models import NewsSource, Article, UserProfile, ArticleView, Comment, PushToken, Category, UserNotification
from .serializers import NewsSourceSerializer, ArticleSerializer, ArticleViewSerializer, UserProfileSerializer, \
    CommentSerializer, CategorySerializer, NotificationStatsSerializer, UserNotificationSerializer
from datetime import datetime, timedelta
import re

from .serializers import (
    PushTokenSerializer,
    PushTokenCreateSerializer,
    TokenUpdateUsageSerializer
)


class CategoriesPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        current_page = self.page.number
        total_pages = self.page.paginator.num_pages
        return Response({
            'count': self.page.paginator.count,
            'next': current_page + 1 if self.page.has_next() else None,
            'previous': current_page - 1 if self.page.has_previous() else None,
            'page_size': self.page_size,
            'total_pages': total_pages,
            'current_page': current_page,
            'results': data
        })


class ArticleSearchPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50

    def get_paginated_response(self, data):
        current_page = self.page.number
        total_pages = self.page.paginator.num_pages
        return Response({
            'count': self.page.paginator.count,
            'next': current_page + 1 if self.page.has_next() else None,
            'previous': current_page - 1 if self.page.has_previous() else None,
            'page_size': self.page_size,
            'total_pages': total_pages,
            'current_page': current_page,
            'results': data
        })


# Views
class NewsSourceViewSet(viewsets.ModelViewSet):
    queryset = NewsSource.objects.filter(is_active=True)
    serializer_class = NewsSourceSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['post'])
    def follow(self, request, pk=None):
        source = self.get_object()
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.followed_sources.add(source)
        return Response({'status': 'followed'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def unfollow(self, request, pk=None):
        source = self.get_object()
        profile = UserProfile.objects.get(user=request.user)
        profile.followed_sources.remove(source)
        return Response({'status': 'unfollowed'}, status=status.HTTP_200_OK)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = CategoriesPagination

    @action(detail=True, methods=['post'])
    def subscribe(self, request, pk=None):
        category = self.get_object()
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.preferred_categories.add(category)
        return Response({'status': 'subscribed'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def unsubscribe(self, request, pk=None):
        category = self.get_object()
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.preferred_categories.remove(category)
        return Response({'status': 'unsubscribed'}, status=status.HTTP_200_OK)


class ArticleViewSet(viewsets.ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ArticleSearchPagination

    def get_queryset(self):
        user = self.request.user
        profile = UserProfile.objects.filter(user=user).first()
        if profile and profile.followed_sources.exists():
            return self.queryset.filter(source__in=profile.followed_sources.all())
        return self.queryset.filter(source__is_active=True)

    def _calculate_article_score(self, queryset, recency_weight=0.4, engagement_weight=0.3,
                                 quality_weight=0.3, time_window_hours=48):
        """
        Calculate a comprehensive score for articles based on multiple factors.

        Scoring factors:
        - Recency: How fresh is the article (exponential decay)
        - Engagement: View count with time normalization
        - Quality: Has full content, word count, read time

        Args:
            queryset: Article queryset to score
            recency_weight: Weight for freshness (0-1)
            engagement_weight: Weight for user engagement (0-1)
            quality_weight: Weight for content quality (0-1)
            time_window_hours: Hours to consider for recent articles
        """
        from django.db.models import ExpressionWrapper, DurationField
        from django.db.models.functions import Extract

        now = timezone.now()
        time_threshold = now - timedelta(hours=time_window_hours)

        # Calculate hours since publication (for recency scoring)
        # Use ExpressionWrapper with DurationField, then extract epoch (seconds)
        queryset = queryset.annotate(
            time_diff=Case(
                When(
                    published_at__isnull=False,
                    then=ExpressionWrapper(
                        now - F('published_at'),
                        output_field=DurationField()
                    )
                ),
                default=ExpressionWrapper(
                    now - F('scraped_at'),
                    output_field=DurationField()
                ),
                output_field=DurationField()
            )
        ).annotate(
            hours_old=ExpressionWrapper(
                Extract('time_diff', 'epoch') / 3600.0,
                output_field=FloatField()
            )
        )

        # Recency score: Exponential decay (newer = higher score)
        # Articles within time_window get max score, then decay
        queryset = queryset.annotate(
            recency_score=Case(
                When(
                    hours_old__lte=6,  # Fresh articles (< 6 hours)
                    then=Value(100.0)
                ),
                When(
                    hours_old__lte=12,  # Recent articles (6-12 hours)
                    then=Value(80.0)
                ),
                When(
                    hours_old__lte=24,  # Today's articles (12-24 hours)
                    then=Value(60.0)
                ),
                When(
                    hours_old__lte=48,  # Yesterday's articles (24-48 hours)
                    then=Value(40.0)
                ),
                When(
                    hours_old__lte=72,  # 2-3 days old
                    then=Value(20.0)
                ),
                default=Value(10.0),  # Older articles
                output_field=FloatField()
            )
        )

        # Engagement score: Views with time normalization
        # Recent views matter more than old views
        queryset = queryset.annotate(
            recent_view_count=Count(
                'views',
                filter=Q(views__viewed_at__gte=time_threshold)
            ),
            total_view_count=Count('views'),
            # Normalize by article age to prevent old popular articles from dominating
            engagement_score=Case(
                When(
                    hours_old__lte=24,
                    then=F('recent_view_count') * 10  # Boost recent articles
                ),
                When(
                    hours_old__lte=48,
                    then=F('recent_view_count') * 5
                ),
                default=F('recent_view_count') * 2,
                output_field=FloatField()
            )
        )

        # Quality score: Content completeness and depth
        queryset = queryset.annotate(
            quality_score=Case(
                When(
                    has_full_content=True,
                    word_count__gte=800,  # Long-form content
                    then=Value(100.0)
                ),
                When(
                    has_full_content=True,
                    word_count__gte=400,  # Medium-form content
                    then=Value(70.0)
                ),
                When(
                    has_full_content=True,
                    then=Value(50.0)
                ),
                When(
                    word_count__gte=200,  # At least some content
                    then=Value(30.0)
                ),
                default=Value(10.0),
                output_field=FloatField()
            )
        )

        # Combined score with weights
        queryset = queryset.annotate(
            article_score=(
                    (F('recency_score') * recency_weight) +
                    (F('engagement_score') * engagement_weight) +
                    (F('quality_score') * quality_weight)
            )
        )

        return queryset

    def _get_excluded_article_ids(self, request):
        """
        Get IDs of articles that should be excluded from results.
        This prevents the top story from appearing in other endpoints.
        """
        excluded_ids = []

        # Check if there's a top story to exclude
        exclude_top = request.query_params.get('exclude_top_story', 'true').lower() == 'true'

        if exclude_top:
            top_story = self.get_queryset().filter(
                has_full_content=True
            ).order_by('-scraped_at').first()

            if top_story:
                excluded_ids.append(top_story.id)

        # Allow client to pass additional IDs to exclude
        additional_excludes = request.query_params.get('exclude_ids', '')
        if additional_excludes:
            try:
                excluded_ids.extend([int(x) for x in additional_excludes.split(',') if x.strip()])
            except ValueError:
                pass

        return excluded_ids

    @action(detail=False, methods=['get'])
    def search(self, request):
        """
        Advanced search endpoint for articles with multiple search strategies.

        GET /api/articles/search/?q=search_term&category=1&source=2&date_from=2024-01-01&date_to=2024-12-31&sort_by=relevance

        Query Parameters:
        - q: Search query (required)
        - category: Filter by category ID
        - source: Filter by news source ID
        - date_from: Start date (YYYY-MM-DD)
        - date_to: End date (YYYY-MM-DD)
        - sort_by: Sort method (relevance, date_desc, date_asc, popularity)
        - has_full_content: Filter articles with full content (true/false)
        """
        query = request.query_params.get('q', '').strip()

        if not query:
            return Response(
                {'error': 'Search query (q) parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Start with base queryset
        queryset = self.get_queryset()

        # Apply filters
        queryset = self._apply_search_filters(queryset, request.query_params)

        # Apply search
        queryset = self._apply_search_query(queryset, query)

        # Apply sorting
        sort_by = request.query_params.get('sort_by', 'relevance')
        queryset = self._apply_search_sorting(queryset, sort_by, query)

        # Paginate results
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def _apply_search_filters(self, queryset, params):
        """Apply additional filters to the search queryset."""

        # Filter by category
        category_id = params.get('category')
        if category_id:
            try:
                queryset = queryset.filter(category_id=int(category_id))
            except (ValueError, TypeError):
                pass

        # Filter by source
        source_id = params.get('source')
        if source_id:
            try:
                queryset = queryset.filter(source_id=int(source_id))
            except (ValueError, TypeError):
                pass

        # Filter by date range
        date_from = params.get('date_from')
        date_to = params.get('date_to')

        if date_from:
            try:
                from_date = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(published_at__date__gte=from_date)
            except ValueError:
                pass

        if date_to:
            try:
                to_date = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(published_at__date__lte=to_date)
            except ValueError:
                pass

        # Filter by full content availability
        has_full_content = params.get('has_full_content')
        if has_full_content and has_full_content.lower() == 'true':
            queryset = queryset.filter(has_full_content=True)

        return queryset

    def _apply_search_query(self, queryset, query):
        """
        Apply search query using multiple strategies for better results.
        This uses both PostgreSQL full-text search and Django Q objects for compatibility.
        """

        # Clean and prepare search terms
        search_terms = self._prepare_search_terms(query)

        # Try PostgreSQL full-text search if available
        try:
            # Full-text search with ranking
            search_vector = SearchVector('title', weight='A') + \
                            SearchVector('excerpt', weight='B') + \
                            SearchVector('content', weight='C')

            search_query = SearchQuery(query)

            queryset = queryset.annotate(
                search=search_vector,
                rank=SearchRank(search_vector, search_query)
            ).filter(search=search_query)

            return queryset

        except Exception:
            # Fallback to basic search using Q objects
            return self._basic_search(queryset, search_terms)

    def _basic_search(self, queryset, search_terms):
        """Fallback search method using Django Q objects."""
        search_q = Q()

        for term in search_terms:
            term_q = (
                    Q(title__icontains=term) |
                    Q(excerpt__icontains=term) |
                    Q(content__icontains=term) |
                    Q(source__name__icontains=term) |
                    Q(category__name__icontains=term) |
                    Q(tags__name__icontains=term)
            )
            search_q |= term_q

        return queryset.filter(search_q).distinct()

    def _prepare_search_terms(self, query):
        """Clean and prepare search terms."""
        # Remove special characters and split into terms
        clean_query = re.sub(r'[^\w\s]', ' ', query)
        terms = [term.strip() for term in clean_query.split() if len(term.strip()) > 2]
        return terms

    def _apply_search_sorting(self, queryset, sort_by, query):
        """Apply sorting to search results."""

        if sort_by == 'relevance':
            # If using PostgreSQL search with rank, sort by rank
            if hasattr(queryset.model, 'rank'):
                return queryset.order_by('-rank', '-scraped_at')
            else:
                # Fallback: prioritize title matches, then date
                return queryset.extra(
                    select={
                        'title_match': f"CASE WHEN LOWER(title) LIKE LOWER('%%{query}%%') THEN 1 ELSE 0 END"
                    }
                ).order_by('-title_match', '-scraped_at')

        elif sort_by == 'date_desc':
            return queryset.order_by('-scraped_at')

        elif sort_by == 'date_asc':
            return queryset.order_by('scraped_at')

        elif sort_by == 'popularity':
            return queryset.annotate(
                view_count=Count('views')
            ).order_by('-view_count', '-scraped_at')

        else:
            # Default to date descending
            return queryset.order_by('-scraped_at')

    @action(detail=False, methods=['get'])
    def search_suggestions(self, request):
        """
        Get search suggestions based on partial query.
        Prioritizes fresh content and trending topics.

        GET /api/articles/search_suggestions/?q=partial_term&limit=10
        """
        query = request.query_params.get('q', '').strip()
        limit = int(request.query_params.get('limit', 10))

        if len(query) < 2:
            return Response({'suggestions': []})

        suggestions = []

        # Prioritize recent articles (last 7 days)
        recent_threshold = timezone.now() - timedelta(days=7)

        # Get suggestions from recent article titles
        title_suggestions = Article.objects.filter(
            title__icontains=query,
            scraped_at__gte=recent_threshold
        ).order_by('-scraped_at').values_list('title', flat=True).distinct()[:limit // 2]

        # Get suggestions from categories
        category_suggestions = Category.objects.filter(
            name__icontains=query
        ).values_list('name', flat=True).distinct()[:limit // 4]

        # Get suggestions from active sources
        source_suggestions = NewsSource.objects.filter(
            name__icontains=query,
            is_active=True
        ).values_list('name', flat=True).distinct()[:limit // 4]

        suggestions.extend(list(title_suggestions))
        suggestions.extend(list(category_suggestions))
        suggestions.extend(list(source_suggestions))

        return Response({
            'suggestions': suggestions[:limit]
        })

    @action(detail=False, methods=['get'])
    def top_story(self, request):
        """
        Get the single most important story right now.
        Prioritizes breaking news and very recent articles with engagement.

        GET /api/articles/top_story/
        """
        # Get articles from last 24 hours with full content
        time_threshold = timezone.now() - timedelta(hours=24)

        queryset = self.get_queryset().filter(
            has_full_content=True,
            scraped_at__gte=time_threshold
        )

        # Score with heavy recency bias for top story
        queryset = self._calculate_article_score(
            queryset,
            recency_weight=0.6,  # Higher recency weight for top story
            engagement_weight=0.3,
            quality_weight=0.1,
            time_window_hours=24
        )

        # Get top scored article
        top_story = queryset.order_by('-article_score').first()

        # Fallback to most recent if no scored articles
        if not top_story:
            top_story = self.get_queryset().filter(
                has_full_content=True
            ).order_by('-scraped_at').first()

        if top_story:
            serializer = self.get_serializer([top_story], many=True)
            return Response(serializer.data)

        return Response([])

    @action(detail=False, methods=['get'])
    def featured(self, request):
        """
        Get featured articles based on comprehensive scoring.
        Balances freshness, engagement, and quality.
        Excludes the top story.

        GET /api/articles/featured/?exclude_top_story=true&exclude_ids=1,2,3
        """
        # Get excluded IDs (including top story)
        excluded_ids = self._get_excluded_article_ids(request)

        # Get articles from last 72 hours with full content
        time_threshold = timezone.now() - timedelta(hours=72)

        queryset = self.get_queryset().filter(
            has_full_content=True,
            scraped_at__gte=time_threshold
        ).exclude(id__in=excluded_ids)

        # Calculate comprehensive score
        queryset = self._calculate_article_score(
            queryset,
            recency_weight=0.4,
            engagement_weight=0.3,
            quality_weight=0.3,
            time_window_hours=48
        )

        # Get top 5 featured articles
        queryset = queryset.order_by('-article_score', '-scraped_at')[:5]

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def top_reads(self, request):
        """
        Get most-read articles from the past week.
        Focuses on engagement with recency consideration.
        Excludes top story and featured articles.

        GET /api/articles/top_reads/?days=7&exclude_top_story=true
        """
        # Get excluded IDs
        excluded_ids = self._get_excluded_article_ids(request)

        # Get time window (default 7 days)
        days = int(request.query_params.get('days', 7))
        time_threshold = timezone.now() - timedelta(days=days)

        queryset = self.get_queryset().filter(
            scraped_at__gte=time_threshold
        ).exclude(id__in=excluded_ids)

        # Calculate score with higher engagement weight
        queryset = self._calculate_article_score(
            queryset,
            recency_weight=0.2,
            engagement_weight=0.6,  # Emphasize engagement for "top reads"
            quality_weight=0.2,
            time_window_hours=days * 24
        )

        # Order by engagement score primarily
        queryset = queryset.order_by('-engagement_score', '-article_score')[:10]

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def latest(self, request):
        """
        Get the latest articles, freshly scraped.
        Pure chronological order with quality filter.
        Excludes top story.

        GET /api/articles/latest/?hours=24&exclude_top_story=true
        """
        # Get excluded IDs
        excluded_ids = self._get_excluded_article_ids(request)

        # Optional time filter (default: all recent)
        hours = request.query_params.get('hours')
        queryset = self.get_queryset()

        if hours:
            try:
                time_threshold = timezone.now() - timedelta(hours=int(hours))
                queryset = queryset.filter(scraped_at__gte=time_threshold)
            except (ValueError, TypeError):
                pass

        # Exclude already featured articles
        queryset = queryset.exclude(id__in=excluded_ids)

        # Prefer articles with full content, but don't require it
        queryset = queryset.annotate(
            has_content=Case(
                When(has_full_content=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).order_by('-has_content', '-scraped_at')[:20]

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def trending(self, request):
        """
        Get trending articles based on recent engagement velocity.
        Articles gaining views quickly.

        GET /api/articles/trending/
        """
        from django.db.models import ExpressionWrapper, DurationField
        from django.db.models.functions import Extract

        # Get excluded IDs
        excluded_ids = self._get_excluded_article_ids(request)

        # Look at last 24 hours
        time_threshold = timezone.now() - timedelta(hours=24)
        now = timezone.now()

        queryset = self.get_queryset().filter(
            scraped_at__gte=time_threshold
        ).exclude(id__in=excluded_ids)

        # Calculate trending score: recent views relative to article age
        queryset = queryset.annotate(
            recent_views=Count('views', filter=Q(views__viewed_at__gte=time_threshold)),
            time_diff=Case(
                When(
                    published_at__isnull=False,
                    then=ExpressionWrapper(
                        now - F('published_at'),
                        output_field=DurationField()
                    )
                ),
                default=ExpressionWrapper(
                    now - F('scraped_at'),
                    output_field=DurationField()
                ),
                output_field=DurationField()
            )
        ).annotate(
            hours_since_published=ExpressionWrapper(
                Extract('time_diff', 'epoch') / 3600.0,
                output_field=FloatField()
            ),
            # Velocity: views per hour (with minimum to avoid division issues)
            trending_score=Case(
                When(
                    hours_since_published__gt=0,
                    then=F('recent_views') / Greatest(F('hours_since_published'), Value(1.0))
                ),
                default=F('recent_views'),
                output_field=FloatField()
            )
        ).filter(
            recent_views__gt=0  # Must have at least some views
        ).order_by('-trending_score', '-recent_views')[:10]

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def view(self, request, pk=None):
        article = self.get_object()
        view = ArticleView.objects.create(
            user=request.user,
            article=article,
            duration_seconds=request.data.get('duration_seconds', 0)
        )
        serializer = ArticleViewSerializer(view)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def batch(self, request):
        """
        POST /api/articles/batch/
        {
            "article_ids": [1, 2, 3, 4]
        }
        """
        article_ids = request.data.get('article_ids', [])
        if not isinstance(article_ids, list) or not article_ids:
            return Response(
                {"error": "A non-empty list of article IDs is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        articles = self.get_queryset().filter(id__in=article_ids).distinct()
        serializer = self.get_serializer(articles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def related(self, request, pk=None):
        """
        Get related articles based on category, tags, and source.
        Prioritizes fresh, related content.
        """
        article = self.get_object()

        # Look for related articles from last 7 days
        time_threshold = timezone.now() - timedelta(days=7)

        queryset = self.get_queryset().filter(
            scraped_at__gte=time_threshold
        ).filter(
            Q(category=article.category) |
            Q(tags__in=article.tags.all()) |
            Q(source=article.source)
        ).exclude(id=article.id).distinct()

        # Score related articles
        queryset = self._calculate_article_score(
            queryset,
            recency_weight=0.5,
            engagement_weight=0.3,
            quality_weight=0.2,
            time_window_hours=168  # 7 days
        )

        queryset = queryset.order_by('-article_score')[:5]

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def comments(self, request, pk=None):
        article = self.get_object()
        # Fetch top-level comments only (parent__isnull=True)
        comments = Comment.objects.filter(
            article=article,
            parent__isnull=True,
            is_approved=True
        ).select_related('user').prefetch_related('replies')
        serializer = CommentSerializer(comments, many=True, context={'request': request})
        return Response(serializer.data)


class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)

    @action(detail=False, methods=['get'])
    def subscribed_categories(self, request):
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        serializer = CategorySerializer(profile.preferred_categories.all(), many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def update_categories(self, request):
        """
        Update user's preferred categories in bulk.
        POST /api/profiles/update_categories/
        {
            "category_ids": [1, 2, 3, 4, 5]
        }
        """
        category_ids = request.data.get('category_ids', [])
        if not isinstance(category_ids, list):
            return Response(
                {'error': 'category_ids must be a list'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(category_ids) < 5:
            return Response(
                {'error': 'At least 5 categories must be selected'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate category IDs
        try:
            categories = Category.objects.filter(id__in=category_ids)
            if len(categories) != len(category_ids):
                return Response(
                    {'error': 'One or more category IDs are invalid'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except ValueError:
            return Response(
                {'error': 'Invalid category IDs provided'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update user's profile
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.preferred_categories.set(categories)
        profile.save()

        serializer = CategorySerializer(profile.preferred_categories.all(), many=True)
        return Response({
            'message': 'Categories updated successfully',
            'categories': serializer.data
        }, status=status.HTTP_200_OK)


class CommentViewSet(viewsets.ModelViewSet):
    queryset = Comment.objects.filter(is_approved=True)
    serializer_class = CommentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Restrict to approved comments; further filtering in actions
        return self.queryset.select_related('user', 'article', 'parent').prefetch_related('replies')

    def create(self, request, *args, **kwargs):
        # Create a top-level comment
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        # Create a reply to an existing comment
        parent_comment = self.get_object()
        data = request.data.copy()
        data['parent'] = parent_comment.id
        data['article'] = parent_comment.article.id  # Ensure reply uses parent's article
        serializer = self.get_serializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class GetOrCreatePushTokenView(generics.CreateAPIView):
    """
    Get or create a push token for the authenticated user.

    POST /api/push-tokens/
    {
        "token": "ExponentPushToken[AQ5CCJA9AMg9mCUx6X_wOH]",
        "device_id": "unique-device-identifier",
        "platform": "ios"  # Optional: ios, android, web
    }
    """
    serializer_class = PushTokenCreateSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_value = serializer.validated_data['token']
        device_id = serializer.validated_data.get('device_id', '')
        platform = serializer.validated_data.get('platform', '')

        # Try to get existing token
        if device_id:
            # If device_id provided, look for existing token for this user+device
            push_token, created = PushToken.objects.get_or_create(
                user=request.user,
                device_id=device_id,
                defaults={
                    'token': token_value,
                    'platform': platform,
                    'is_active': True,
                    'last_used': timezone.now()
                }
            )

            if not created:
                # Update existing token if it changed
                if push_token.token != token_value:
                    push_token.token = token_value
                    push_token.platform = platform
                    push_token.is_active = True
                push_token.last_used = timezone.now()
                push_token.save()
        else:
            # No device_id provided, check if token already exists for this user
            try:
                push_token = PushToken.objects.get(user=request.user, token=token_value)
                push_token.last_used = timezone.now()
                push_token.is_active = True
                if platform:
                    push_token.platform = platform
                push_token.save()
                created = False
            except PushToken.DoesNotExist:
                push_token = PushToken.objects.create(
                    user=request.user,
                    token=token_value,
                    device_id=device_id,
                    platform=platform,
                    is_active=True,
                    last_used=timezone.now()
                )
                created = True

        response_serializer = PushTokenSerializer(push_token)
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK

        return Response({
            'token': response_serializer.data,
            'created': created,
            'message': 'Token created successfully' if created else 'Token updated successfully'
        }, status=response_status)


class ListUserPushTokensView(generics.ListAPIView):
    """
    Get all active push tokens for the authenticated user.

    GET /api/push-tokens/list/
    """
    serializer_class = PushTokenSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PushToken.objects.filter(user=self.request.user, is_active=True)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)

        return Response({
            'tokens': serializer.data,
            'count': queryset.count()
        })


class DeactivatePushTokenView(generics.DestroyAPIView):
    """
    Deactivate a push token by ID (soft delete).

    DELETE /api/push-tokens/<token_id>/
    """
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return PushToken.objects.filter(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save()

        return Response({
            'message': 'Token deactivated successfully'
        }, status=status.HTTP_200_OK)


class DeactivatePushTokenByValueView(views.APIView):
    """
    Deactivate a push token by token value (soft delete).

    DELETE /api/push-tokens/deactivate/
    {
        "token": "ExponentPushToken[AQ5CCJA9AMg9mCUx6X_wOH]"
    }
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        token_value = request.data.get('token')
        if not token_value:
            return Response({
                'error': 'Token value required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            push_token = PushToken.objects.get(
                token=token_value,
                user=request.user
            )
            push_token.is_active = False
            push_token.save()

            return Response({
                'message': 'Token deactivated successfully'
            }, status=status.HTTP_200_OK)

        except PushToken.DoesNotExist:
            return Response({
                'error': 'Token not found'
            }, status=status.HTTP_404_NOT_FOUND)


class UpdateTokenUsageView(views.APIView):
    """
    Update last_used timestamp for a token (useful for tracking active tokens).

    POST /api/push-tokens/update-usage/
    {
        "token": "ExponentPushToken[AQ5CCJA9AMg9mCUx6X_wOH]"
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TokenUpdateUsageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_value = serializer.validated_data['token']

        try:
            push_token = PushToken.objects.get(
                token=token_value,
                user=request.user,
                is_active=True
            )
            push_token.last_used = timezone.now()
            push_token.save()

            return Response({
                'message': 'Token usage updated successfully',
                'last_used': push_token.last_used
            }, status=status.HTTP_200_OK)

        except PushToken.DoesNotExist:
            return Response({
                'error': 'Active token not found'
            }, status=status.HTTP_404_NOT_FOUND)


class PushTokenDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    Retrieve, update, or deactivate a specific push token.

    GET /api/push-tokens/<token_id>/detail/
    PUT/PATCH /api/push-tokens/<token_id>/detail/
    DELETE /api/push-tokens/<token_id>/detail/
    """
    serializer_class = PushTokenSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return PushToken.objects.filter(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """Soft delete - deactivate instead of actual deletion"""
        instance = self.get_object()
        instance.is_active = False
        instance.save()

        return Response({
            'message': 'Token deactivated successfully'
        }, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """Override to update last_used timestamp on updates"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        # Update last_used timestamp
        instance.last_used = timezone.now()
        self.perform_update(serializer)

        return Response(serializer.data)


class BulkDeactivateTokensView(views.APIView):
    """
    Deactivate multiple tokens at once.

    POST /api/push-tokens/bulk-deactivate/
    {
        "token_ids": [1, 2, 3]
    }
    OR
    {
        "tokens": ["ExponentPushToken[...]", "ExponentPushToken[...]"]
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token_ids = request.data.get('token_ids', [])
        tokens = request.data.get('tokens', [])

        if not token_ids and not tokens:
            return Response({
                'error': 'Either token_ids or tokens array required'
            }, status=status.HTTP_400_BAD_REQUEST)

        updated_count = 0

        if token_ids:
            updated_count = PushToken.objects.filter(
                id__in=token_ids,
                user=request.user
            ).update(is_active=False)

        if tokens:
            updated_count += PushToken.objects.filter(
                token__in=tokens,
                user=request.user
            ).update(is_active=False)

        return Response({
            'message': f'{updated_count} tokens deactivated successfully',
            'deactivated_count': updated_count
        }, status=status.HTTP_200_OK)


class UserNotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for user notifications

    Endpoints:
    - GET /api/notifications/ - List all notifications for current user
    - GET /api/notifications/unread/ - List unread notifications
    - GET /api/notifications/stats/ - Get notification statistics
    - POST /api/notifications/{id}/mark_read/ - Mark a notification as read
    - POST /api/notifications/mark_all_read/ - Mark all as read
    """

    serializer_class = UserNotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Get notifications for the current user"""
        return UserNotification.objects.filter(
            user=self.request.user
        ).prefetch_related('articles', 'articles__source', 'articles__category').order_by('-sent_at')

    @action(detail=False, methods=['get'])
    def unread(self, request):
        """Get only unread notifications"""
        queryset = self.get_queryset().filter(is_read=False)
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get notification statistics for the user"""
        queryset = self.get_queryset()

        unread_count = queryset.filter(is_read=False).count()
        total_count = queryset.count()
        latest = queryset.first()

        # Serialize the latest notification separately before including in stats
        latest_notification_data = None
        if latest:
            latest_notification_data = UserNotificationSerializer(latest).data

        data = {
            'unread_count': unread_count,
            'total_count': total_count,
            'latest_notification': latest_notification_data
        }

        # Return the data directly (already properly formatted)
        return Response(data)

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark a specific notification as read"""
        notification = self.get_object()

        if notification.user != request.user:
            return Response(
                {'error': 'You do not have permission to modify this notification'},
                status=status.HTTP_403_FORBIDDEN
            )

        notification.mark_as_read()

        return Response({
            'status': 'success',
            'message': 'Notification marked as read',
            'notification': self.get_serializer(notification).data
        })

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        """Mark all notifications as read for the current user"""
        from django.utils import timezone

        updated_count = UserNotification.objects.filter(
            user=request.user,
            is_read=False
        ).update(
            is_read=True,
            read_at=timezone.now()
        )

        return Response({
            'status': 'success',
            'message': f'Marked {updated_count} notifications as read',
            'updated_count': updated_count
        })

    @action(detail=False, methods=['delete'])
    def clear_old(self, request):
        """Delete notifications older than 30 days"""
        from django.utils import timezone
        from datetime import timedelta

        cutoff_date = timezone.now() - timedelta(days=30)
        deleted_count, _ = UserNotification.objects.filter(
            user=request.user,
            sent_at__lt=cutoff_date
        ).delete()

        return Response({
            'status': 'success',
            'message': f'Deleted {deleted_count} old notifications',
            'deleted_count': deleted_count
        })