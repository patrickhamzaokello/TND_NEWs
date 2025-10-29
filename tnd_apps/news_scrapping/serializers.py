from rest_framework import serializers
from .models import NewsSource, Comment, Category, Tag, Author, Article, UserProfile, ArticleView, PushToken,UserNotification


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


class PushTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushToken
        fields = ['id', 'token', 'device_id', 'platform', 'is_active', 'last_used', 'created_at']
        read_only_fields = ['id', 'last_used', 'created_at']

    def validate_token(self, value):
        """Validate push token format"""
        if not value.strip():
            raise serializers.ValidationError("Token cannot be empty.")

        # Validate Expo push token format
        if value.startswith('ExponentPushToken['):
            if not value.endswith(']') or len(value) < 20:
                raise serializers.ValidationError("Invalid Expo push token format.")

        return value


class PushTokenCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushToken
        fields = ['token', 'device_id', 'platform']

    def validate_token(self, value):
        """Validate push token format"""
        if not value.strip():
            raise serializers.ValidationError("Token cannot be empty.")

        # Validate Expo push token format
        if value.startswith('ExponentPushToken['):
            if not value.endswith(']') or len(value) < 20:
                raise serializers.ValidationError("Invalid Expo push token format.")

        return value


class TokenUpdateUsageSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)

    def validate_token(self, value):
        if not value.strip():
            raise serializers.ValidationError("Token cannot be empty.")
        return value

class CommentSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)  # Display username
    replies = serializers.SerializerMethodField()  # Nested replies
    article = serializers.PrimaryKeyRelatedField(queryset=Article.objects.all())

    class Meta:
        model = Comment
        fields = ['id', 'article', 'user', 'content', 'parent', 'created_at', 'updated_at', 'is_approved', 'replies']
        read_only_fields = ['user', 'created_at', 'updated_at', 'is_approved']

    def get_replies(self, obj):
        # Recursively serialize replies (only if they exist)
        if obj.replies.exists():
            return CommentSerializer(obj.replies.filter(is_approved=True), many=True).data
        return []

    def validate(self, data):
        # Ensure parent comment belongs to the same article
        if data.get('parent') and data['parent'].article != data['article']:
            raise serializers.ValidationError("Reply must belong to the same article as the parent comment.")
        return data

    def create(self, validated_data):
        # Set the user from the request context
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)

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


class NotificationArticleSerializer(serializers.ModelSerializer):
    """Simplified article serializer for notifications"""

    class Meta:
        model = Article
        fields = [
            'id', 'title', 'slug', 'excerpt', 'featured_image_url',
            'source', 'category', 'published_at', 'read_time_minutes'
        ]
        depth = 1  # Include nested source and category details


class UserNotificationSerializer(serializers.ModelSerializer):
    """Serializer for user notifications"""

    articles = NotificationArticleSerializer(many=True, read_only=True)
    article_count = serializers.SerializerMethodField()
    time_ago = serializers.SerializerMethodField()

    class Meta:
        model = UserNotification
        fields = [
            'id', 'notification_type', 'title', 'body',
            'articles', 'article_count', 'is_read', 'read_at',
            'sent_at', 'time_ago', 'priority', 'metadata'
        ]

    def get_article_count(self, obj):
        return obj.articles.count()

    def get_time_ago(self, obj):
        """Human-readable time since notification was sent"""
        from django.utils.timesince import timesince
        return timesince(obj.sent_at)


class NotificationStatsSerializer(serializers.Serializer):
    """Serializer for notification statistics"""

    unread_count = serializers.IntegerField()
    total_count = serializers.IntegerField()
    latest_notification = UserNotificationSerializer(allow_null=True)