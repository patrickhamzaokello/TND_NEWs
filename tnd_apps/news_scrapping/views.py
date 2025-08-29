from django.shortcuts import render

# Create your views here.
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth.models import User
from django.db.models import Count, Q
from datetime import timedelta
from django.utils import timezone
from .models import NewsSource, Category, Tag, Author, Article, UserProfile, ArticleView


# Serializers
class NewsSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsSource
        fields = ['id', 'name', 'base_url', 'news_url', 'is_active']


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug']


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ['id', 'name', 'slug']


class AuthorSerializer(serializers.ModelSerializer):
    source = NewsSourceSerializer(read_only=True)

    class Meta:
        model = Author
        fields = ['id', 'name', 'profile_url', 'source']


class ArticleSerializer(serializers.ModelSerializer):
    source = NewsSourceSerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    author = AuthorSerializer(read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    view_count = serializers.SerializerMethodField()

    def get_view_count(self, obj):
        return obj.views.count()

    class Meta:
        model = Article
        fields = [
            'id', 'external_id', 'url', 'title', 'slug', 'excerpt', 'content',
            'word_count', 'read_time_minutes', 'featured_image_url', 'image_caption',
            'source', 'category', 'author', 'tags', 'published_at', 'scraped_at',
            'has_full_content', 'view_count'
        ]


class UserProfileSerializer(serializers.ModelSerializer):
    followed_sources = NewsSourceSerializer(many=True, read_only=True)
    preferred_categories = CategorySerializer(many=True, read_only=True)

    class Meta:
        model = UserProfile
        fields = ['id', 'user', 'followed_sources', 'preferred_categories']


class ArticleViewSerializer(serializers.ModelSerializer):
    article = ArticleSerializer(read_only=True)

    class Meta:
        model = ArticleView
        fields = ['id', 'user', 'article', 'viewed_at', 'duration_seconds']


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


class ArticleViewSet(viewsets.ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        profile = UserProfile.objects.filter(user=user).first()
        if profile and profile.followed_sources.exists():
            return self.queryset.filter(source__in=profile.followed_sources.all())
        return self.queryset.filter(source__is_active=True)

    @action(detail=False, methods=['get'])
    def top_story(self, request):
        queryset = self.get_queryset().filter(has_full_content=True).order_by('-published_at')[:1]
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def featured(self, request):
        queryset = self.get_queryset().filter(has_full_content=True).annotate(
            view_count=Count('views')
        ).order_by('-view_count', '-published_at')[:5]
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def top_reads(self, request):
        time_threshold = timezone.now() - timedelta(days=7)
        queryset = self.get_queryset().annotate(
            view_count=Count('views')
        ).filter(views__viewed_at__gte=time_threshold).order_by('-view_count')[:10]
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def latest(self, request):
        queryset = self.get_queryset().order_by('-published_at')[:20]
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

    @action(detail=True, methods=['get'])
    def related(self, request, pk=None):
        article = self.get_object()
        queryset = self.get_queryset().filter(
            Q(category=article.category) | Q(tags__in=article.tags.all()) | Q(source=article.source)
        ).exclude(id=article.id).distinct()[:5]
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)


# URLs
from django.urls import path, include
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'sources', NewsSourceViewSet)
router.register(r'articles', ArticleViewSet)
router.register(r'profiles', UserProfileViewSet)

urlpatterns = [
    path('', include(router.urls)),
]