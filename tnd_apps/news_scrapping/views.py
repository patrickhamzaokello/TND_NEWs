
# Create your views here.
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Q
from datetime import timedelta
from django.utils import timezone
from .models import NewsSource, Article, UserProfile, ArticleView, Comment
from .serializers import NewsSourceSerializer, ArticleSerializer, ArticleViewSerializer, UserProfileSerializer, \
    CommentSerializer


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

    @action(detail=True, methods=['get'])
    def comments(self, request, pk=None):
        article = self.get_object()
        # Fetch top-level comments only (parent__isnull=True)
        comments = Comment.objects.filter(article=article, parent__isnull=True, is_approved=True).select_related('user').prefetch_related('replies')
        serializer = CommentSerializer(comments, many=True, context={'request': request})
        return Response(serializer.data)

class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)


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