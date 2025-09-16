
# Create your views here.
from rest_framework import serializers, viewsets, status,generics, status, views
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Q
from datetime import timedelta
from django.utils import timezone
from .models import NewsSource, Article, UserProfile, ArticleView, Comment, PushToken, Category
from .serializers import NewsSourceSerializer, ArticleSerializer, ArticleViewSerializer, UserProfileSerializer, \
    CommentSerializer, CategorySerializer

from .serializers import (
    PushTokenSerializer,
    PushTokenCreateSerializer,
    TokenUpdateUsageSerializer
)

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

    @action(detail=False, methods=['get'])
    def subscribed_categories(self, request):
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        serializer = CategorySerializer(profile.preferred_categories.all(), many=True)
        return Response(serializer.data)


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