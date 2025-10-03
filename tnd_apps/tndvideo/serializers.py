
# Serializers for API responses

from rest_framework import serializers

from tnd_apps.tndvideo.models import VideoQuality, Video
from tnd_apps.tndvideo.views import validate_video_file


class VideoQualitySerializer(serializers.ModelSerializer):
    """Serializer for VideoQuality model"""

    class Meta:
        model = VideoQuality
        fields = [
            'quality', 'resolution_width', 'resolution_height',
            'bitrate', 'total_segments', 'is_processed'
        ]


class VideoSerializer(serializers.ModelSerializer):
    """Serializer for Video model"""

    qualities = VideoQualitySerializer(many=True, read_only=True)
    duration_formatted = serializers.SerializerMethodField()
    stream_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            'id', 'slug', 'title', 'description', 'status',
            'duration_seconds', 'duration_formatted', 'width', 'height',
            'view_count', 'is_featured', 'created_at', 'published_at',
            'thumbnail_url', 'stream_url', 'qualities', 'category'
        ]
        read_only_fields = ['id', 'slug', 'status', 'view_count']

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
    description = serializers.CharField(required=False, allow_blank=True)
    video_file = serializers.FileField()
    category_id = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(
        choices=['low', 'normal', 'high', 'urgent'],
        default='normal'
    )

    def validate_video_file(self, value):
        is_valid, error = validate_video_file(value)
        if not is_valid:
            raise serializers.ValidationError(error)
        return value


class VideoViewTrackingSerializer(serializers.Serializer):
    """Serializer for tracking video views"""

    watch_duration_seconds = serializers.FloatField(min_value=0)
    last_position_seconds = serializers.FloatField(min_value=0)
    quality_watched = serializers.ChoiceField(
        choices=['low', 'medium', 'high'],
        default='medium'
    )
    session_id = serializers.CharField(max_length=100, required=False)