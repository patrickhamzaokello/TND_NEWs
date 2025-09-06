from rest_framework import serializers
from .models import NewsSource,Comment, Category, Tag, Author, Article, UserProfile, ArticleView


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

