"""
Serializers for video API responses
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import Video, VideoQuality, VideoView, Category
from .utils import validate_video_file
from ..news_scrapping.serializers import CategorySerializer

User = get_user_model()


class VideoQualitySerializer(serializers.ModelSerializer):
    """Serializer for VideoQuality model"""

    file_size_mb = serializers.SerializerMethodField()

    class Meta:
        model = VideoQuality
        fields = [
            'id', 'quality', 'resolution_width', 'resolution_height',
            'bitrate', 'total_segments', 'is_processed', 'file_size_mb'
        ]

    def get_file_size_mb(self, obj):
        """Calculate file size in MB if available"""
        # This assumes you have file size stored somewhere
        # Adjust based on your model structure
        return None


class UserSerializer(serializers.ModelSerializer):
    """Basic user serializer for video responses"""

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'name']
        read_only_fields = fields


class VideoListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for video lists"""

    thumbnail_url = serializers.SerializerMethodField()
    duration_formatted = serializers.SerializerMethodField()
    uploaded_by = UserSerializer(read_only=True)
    category = CategorySerializer(read_only=True)

    class Meta:
        model = Video
        fields = [
            'id', 'slug', 'title', 'description', 'status',
            'duration_seconds', 'duration_formatted', 'view_count',
            'is_featured', 'created_at', 'published_at',
            'thumbnail_url', 'uploaded_by', 'category'
        ]
        read_only_fields = ['id', 'slug', 'status', 'view_count']

    def get_thumbnail_url(self, obj):
        if obj.thumbnail_file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.thumbnail_file.url)
            return obj.thumbnail_file.url
        return None

    def get_duration_formatted(self, obj):
        return obj.get_duration_formatted()


class VideoSerializer(serializers.ModelSerializer):
    """Detailed serializer for single video retrieval"""

    qualities = VideoQualitySerializer(many=True, read_only=True)
    duration_formatted = serializers.SerializerMethodField()
    stream_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    uploaded_by = UserSerializer(read_only=True)
    category = CategorySerializer(read_only=True)

    class Meta:
        model = Video
        fields = [
            'id', 'slug', 'title', 'description', 'status',
            'duration_seconds', 'duration_formatted', 'width', 'height',
            'view_count', 'total_watch_time_seconds', 'is_featured',
            'is_active', 'created_at', 'published_at', 'updated_at',
            'thumbnail_url', 'stream_url', 'qualities', 'category',
            'uploaded_by', 'processing_progress', 'processing_error'
        ]
        read_only_fields = [
            'id', 'slug', 'status', 'view_count', 'total_watch_time_seconds',
            'processing_progress', 'processing_error', 'created_at', 'updated_at'
        ]

    def get_duration_formatted(self, obj):
        return obj.get_duration_formatted()

    def get_stream_url(self, obj):
        if obj.status != 'ready' or not obj.master_playlist_path:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(f'/media/{obj.master_playlist_path}')
        return f'/media/{obj.master_playlist_path}'

    def get_thumbnail_url(self, obj):
        if obj.thumbnail_file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.thumbnail_file.url)
            return obj.thumbnail_file.url
        return None


class VideoUploadSerializer(serializers.Serializer):
    """Serializer for video upload"""

    title = serializers.CharField(max_length=500)
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        default=''
    )
    video_file = serializers.FileField()
    category_id = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(
        choices=['low', 'normal', 'high', 'urgent'],
        default='normal',
        required=False
    )

    def validate_video_file(self, value):
        """Validate video file"""
        is_valid, error = validate_video_file(value)
        if not is_valid:
            raise serializers.ValidationError(error)
        return value

    def validate_category_id(self, value):
        """Validate category exists"""
        if value:
            try:
                Category.objects.get(id=value)
            except Category.DoesNotExist:
                raise serializers.ValidationError("Category does not exist")
        return value


class VideoUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating video metadata"""

    class Meta:
        model = Video
        fields = [
            'title', 'description', 'category',
            'is_featured', 'is_active'
        ]

    def validate_category(self, value):
        """Ensure category exists"""
        if value and not Category.objects.filter(id=value.id).exists():
            raise serializers.ValidationError("Category does not exist")
        return value


class VideoViewTrackingSerializer(serializers.Serializer):
    """Serializer for tracking video views"""

    watch_duration_seconds = serializers.FloatField(min_value=0)
    last_position_seconds = serializers.FloatField(min_value=0)
    quality_watched = serializers.ChoiceField(
        choices=['low', 'medium', 'high'],
        default='medium'
    )
    session_id = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True
    )

    def validate(self, data):
        """Validate watch duration doesn't exceed position"""
        if data['watch_duration_seconds'] > data['last_position_seconds']:
            # Allow this, user might skip around
            pass
        return data


class VideoAnalyticsSerializer(serializers.Serializer):
    """Serializer for video analytics response"""

    video_id = serializers.UUIDField()
    title = serializers.CharField()
    status = serializers.CharField()
    duration_seconds = serializers.IntegerField()
    total_views = serializers.IntegerField()
    unique_users = serializers.IntegerField()
    total_watch_time_seconds = serializers.FloatField()
    average_watch_time_seconds = serializers.FloatField()
    completion_rate = serializers.FloatField()
    engagement_rate = serializers.FloatField()
    device_breakdown = serializers.DictField()
    quality_breakdown = serializers.DictField()


class BulkProcessSerializer(serializers.Serializer):
    """Serializer for bulk video processing"""

    video_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=50  # Limit bulk operations
    )
    priority = serializers.ChoiceField(
        choices=['low', 'normal', 'high', 'urgent'],
        default='normal',
        required=False
    )

    def validate_video_ids(self, value):
        """Ensure no duplicate IDs"""
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Duplicate video IDs found")
        return value


class VideoProcessingStatusSerializer(serializers.Serializer):
    """Serializer for video processing status response"""

    id = serializers.UUIDField()
    title = serializers.CharField()
    status = serializers.CharField()
    progress = serializers.IntegerField()
    error = serializers.CharField(allow_null=True)
    queue_status = serializers.CharField(required=False)
    current_step = serializers.CharField(required=False)
    queue_position = serializers.IntegerField(required=False)
    started_at = serializers.DateTimeField(required=False)
    estimated_completion = serializers.CharField(required=False)
    master_playlist_url = serializers.CharField(required=False, allow_null=True)
    thumbnail_url = serializers.CharField(required=False, allow_null=True)
    duration = serializers.CharField(required=False)
    qualities = VideoQualitySerializer(many=True, required=False)


class VideoViewSerializer(serializers.ModelSerializer):
    """Serializer for VideoView model"""

    user = UserSerializer(read_only=True)
    video_title = serializers.CharField(source='video.title', read_only=True)

    class Meta:
        model = VideoView
        fields = [
            'id', 'video', 'video_title', 'user', 'session_id',
            'watch_duration_seconds', 'last_position_seconds',
            'quality_watched', 'device_type', 'is_completed',
            'updated_at'
        ]
        read_only_fields = fields


class VideoStatsSerializer(serializers.Serializer):
    """Serializer for quick video stats"""

    total_videos = serializers.IntegerField()
    processing_videos = serializers.IntegerField()
    ready_videos = serializers.IntegerField()
    failed_videos = serializers.IntegerField()
    total_views = serializers.IntegerField()
    total_watch_time_hours = serializers.FloatField()