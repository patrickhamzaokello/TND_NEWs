from rest_framework import serializers
from .models import DailyDigest
from ..news_scrapping.models import Article


class ArticleSnippetSerializer(serializers.ModelSerializer):
    """
    Lightweight article representation for embedding inside digest JSON fields.
    Used wherever an article_id appears in top_stories, story_threads, under_radar_story.
    """
    source_name = serializers.CharField(source='source.name', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default=None)
    author_name = serializers.CharField(source='author.name', read_only=True, default=None)

    class Meta:
        model = Article
        fields = [
            'id',
            'title',
            'url',
            'excerpt',
            'featured_image_url',
            'source_name',
            'category_name',
            'author_name',
            'published_at',
            'read_time_minutes',
        ]


def _build_article_map(article_ids: list[int]) -> dict[int, dict]:
    """
    Fetch a batch of Articles by ID and return a dict keyed by id.
    Single DB query regardless of how many IDs are passed.
    """
    articles = Article.objects.select_related('source', 'category', 'author').filter(
        id__in=article_ids
    )
    return {
        article.id: ArticleSnippetSerializer(article).data
        for article in articles
    }


def _collect_article_ids(obj: DailyDigest) -> list[int]:
    """Walk all JSON fields and collect every article_id mentioned."""
    ids = set()

    # top_stories: list of {article_id, ...}
    for story in (obj.top_stories or []):
        if aid := story.get('article_id'):
            ids.add(aid)

    # story_threads: list of {article_ids: [...], ...}
    for thread in (obj.story_threads or []):
        for aid in thread.get('article_ids', []):
            ids.add(aid)

    # under_radar_story: {article_id, ...}
    if obj.under_radar_story:
        if aid := obj.under_radar_story.get('article_id'):
            ids.add(aid)

    return list(ids)


class DailyDigestListSerializer(serializers.ModelSerializer):
    digest_text_excerpt = serializers.SerializerMethodField()

    class Meta:
        model = DailyDigest
        fields = [
            'id',
            'digest_date',
            'articles_analyzed',
            'is_published',
            'generated_at',
            'created_at',
            'digest_text_excerpt',
        ]
        read_only_fields = fields

    def get_digest_text_excerpt(self, obj):
        if obj.digest_text:
            return obj.digest_text[:220] + "..." if len(obj.digest_text) > 220 else obj.digest_text
        return ""


class DailyDigestDetailSerializer(serializers.ModelSerializer):
    """
    Full digest detail with article IDs enriched to include human-readable
    article metadata (title, url, excerpt, image, source, category, author, etc.)
    """
    top_stories = serializers.SerializerMethodField()
    story_threads = serializers.SerializerMethodField()
    under_radar_story = serializers.SerializerMethodField()

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

    def _get_article_map(self, obj) -> dict[int, dict]:
        """
        Build the article map once and cache it on the serializer context
        so top_stories, story_threads, and under_radar_story share one DB hit.
        """
        cache_key = f'_article_map_{obj.pk}'
        if cache_key not in self.context:
            ids = _collect_article_ids(obj)
            self.context[cache_key] = _build_article_map(ids)
        return self.context[cache_key]

    def get_top_stories(self, obj) -> list:
        article_map = self._get_article_map(obj)
        enriched = []
        for story in (obj.top_stories or []):
            entry = dict(story)  # don't mutate the original JSON
            aid = entry.pop('article_id', None)
            if aid is not None:
                entry['article'] = article_map.get(aid)
            enriched.append(entry)
        return enriched

    def get_story_threads(self, obj) -> list:
        article_map = self._get_article_map(obj)
        enriched = []
        for thread in (obj.story_threads or []):
            entry = dict(thread)
            entry['articles'] = [
                article_map.get(aid)
                for aid in entry.pop('article_ids', [])
                if aid in article_map
            ]
            enriched.append(entry)
        return enriched

    def get_under_radar_story(self, obj) -> dict | None:
        if not obj.under_radar_story:
            return None
        article_map = self._get_article_map(obj)
        entry = dict(obj.under_radar_story)
        aid = entry.pop('article_id', None)
        if aid is not None:
            entry['article'] = article_map.get(aid)
        return entry