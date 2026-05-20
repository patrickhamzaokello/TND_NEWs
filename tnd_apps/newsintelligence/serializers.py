from rest_framework import serializers
from .models import DailyDigest, Entity, StoryAlert, StoryCluster, StoryTimelineEvent, SourcePerspective
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
        id__in=article_ids,
        has_full_content=True,
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
            'citations',
            'under_radar_story',
            'key_concern',
            'articles_analyzed',
            'input_tokens_used',
            'output_tokens_used',
            'model_used',
            'is_published',
            'editorial_review_status',
            'reviewed_by',
            'reviewed_at',
            'generated_at',
            'created_at',
        ]
        read_only_fields = fields

    def _get_article_map(self, obj) -> dict[int, dict]:
        """
        Build the article map once per serializer instance so top_stories,
        story_threads, and under_radar_story share one DB hit.
        """
        if not hasattr(self, '_article_map_cache'):
            ids = _collect_article_ids(obj)
            self._article_map_cache = _build_article_map(ids)
        return self._article_map_cache

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


class EntitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Entity
        fields = ['id', 'name', 'normalized_name', 'entity_type', 'aliases', 'description']


class EntityTopArticleSerializer(serializers.ModelSerializer):
    source = serializers.CharField(source='source.name', read_only=True)

    class Meta:
        model = Article
        fields = [
            'id',
            'title',
            'slug',
            'excerpt',
            'source',
            'featured_image_url',
        ]


class SourcePerspectiveSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name', read_only=True)
    article = serializers.SerializerMethodField()
    article_title = serializers.SerializerMethodField()
    article_url = serializers.SerializerMethodField()

    class Meta:
        model = SourcePerspective
        fields = [
            'id', 'source', 'source_name', 'article', 'article_title',
            'article_url', 'framing_summary', 'notable_emphasis',
            'omitted_context', 'sentiment_score', 'created_at',
        ]

    def _full_content_article(self, obj):
        article = getattr(obj, 'article', None)
        if article and article.has_full_content:
            return article
        return None

    def get_article(self, obj):
        article = self._full_content_article(obj)
        return article.id if article else None

    def get_article_title(self, obj):
        article = self._full_content_article(obj)
        return article.title if article else None

    def get_article_url(self, obj):
        article = self._full_content_article(obj)
        return article.url if article else None


class StoryTimelineEventSerializer(serializers.ModelSerializer):
    article = serializers.SerializerMethodField()

    class Meta:
        model = StoryTimelineEvent
        fields = ['id', 'event_date', 'title', 'description', 'article', 'citations', 'created_at']

    def get_article(self, obj):
        article = getattr(obj, 'article', None)
        if article and article.has_full_content:
            return ArticleSnippetSerializer(article).data
        return None


class StoryClusterListSerializer(serializers.ModelSerializer):
    article_count = serializers.IntegerField(read_only=True)
    source_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = StoryCluster
        fields = [
            'id', 'title', 'slug', 'summary', 'why_this_matters',
            'local_impact', 'primary_theme', 'status', 'importance_score',
            'first_seen_at', 'last_seen_at', 'article_count', 'source_count',
        ]


class StoryClusterDetailSerializer(StoryClusterListSerializer):
    articles = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    perspectives = SourcePerspectiveSerializer(source='source_perspectives', many=True, read_only=True)

    class Meta(StoryClusterListSerializer.Meta):
        fields = StoryClusterListSerializer.Meta.fields + ['articles', 'timeline', 'perspectives']

    def get_articles(self, obj):
        articles = [
            link.article
            for link in obj.cluster_articles.select_related('article__source', 'article__category', 'article__author').all()
            if link.article.has_full_content
        ]
        return ArticleSnippetSerializer(articles, many=True).data

    def get_timeline(self, obj):
        events = obj.timeline_events.select_related('article__source', 'article__category', 'article__author').filter(
            article__has_full_content=True
        )
        return StoryTimelineEventSerializer(events, many=True).data


class StoryAlertSerializer(serializers.ModelSerializer):
    article = serializers.SerializerMethodField()
    cluster_title = serializers.CharField(source='cluster.title', read_only=True)

    class Meta:
        model = StoryAlert
        fields = [
            'id', 'cluster', 'cluster_title', 'article', 'title', 'reason',
            'importance_score', 'status', 'created_at', 'sent_at',
        ]

    def get_article(self, obj):
        article = getattr(obj, 'article', None)
        if article and article.has_full_content:
            return ArticleSnippetSerializer(article).data
        return None
