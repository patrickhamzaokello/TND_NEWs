# serializers.py
from rest_framework import serializers
from .models import DailyDigest


class DailyDigestListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list view (overview cards / feed)
    """
    class Meta:
        model = DailyDigest
        fields = [
            'id',
            'digest_date',
            'articles_analyzed',
            'is_published',
            'generated_at',
            'created_at',
            # Optional: excerpt of the digest
            'digest_text_excerpt',
        ]
        read_only_fields = fields

    digest_text_excerpt = serializers.SerializerMethodField()

    def get_digest_text_excerpt(self, obj):
        if obj.digest_text:
            return obj.digest_text[:220] + "..." if len(obj.digest_text) > 220 else obj.digest_text
        return ""


class DailyDigestDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for single digest detail view
    """
    class Meta:
        model = DailyDigest
        fields = [
            'id',
            'digest_date',
            'digest_text',
            'top_stories',
            'trending_entities',
            'sector_sentiment',
            'story_threads',
            'under_radar_story',
            'key_concern',
            'articles_analyzed',
            'input_tokens_used',
            'output_tokens_used',
            'model_used',
            'is_published',
            'generated_at',
            'created_at',
        ]
        read_only_fields = fields