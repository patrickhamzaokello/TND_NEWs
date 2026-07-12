from rest_framework import serializers
from .models import DailyDigest, Entity, StoryAlert, StoryCluster, StoryTimelineEvent, SourcePerspective
from ..news_scrapping.models import Article
from ..news_scrapping.serializers import CleanArticleTextRepresentationMixin
from ..news_scrapping.text_cleaning import clean_article_text


class ArticleSnippetSerializer(CleanArticleTextRepresentationMixin, serializers.ModelSerializer):
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
    illustration_url = serializers.SerializerMethodField()

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
            'illustration_url',
            'illustration_caption',
        ]
        read_only_fields = fields

    def get_digest_text_excerpt(self, obj):
        if obj.digest_text:
            return obj.digest_text[:220] + "..." if len(obj.digest_text) > 220 else obj.digest_text
        return ""

    def get_illustration_url(self, obj):
        if obj.illustration:
            request = self.context.get('request')
            return request.build_absolute_uri(obj.illustration.url) if request else obj.illustration.url
        return None


class DailyDigestDetailSerializer(serializers.ModelSerializer):
    """
    Full digest detail with article IDs enriched to include human-readable
    article metadata (title, url, excerpt, image, source, category, author, etc.)
    """
    top_stories = serializers.SerializerMethodField()
    story_threads = serializers.SerializerMethodField()
    under_radar_story = serializers.SerializerMethodField()
    illustration_url = serializers.SerializerMethodField()

    class Meta:
        model = DailyDigest
        fields = [
            'id',
            'digest_date',
            'digest_text',
            'illustration_url',
            'illustration_caption',
            'illustration_generated_at',
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

    def get_illustration_url(self, obj):
        if obj.illustration:
            request = self.context.get('request')
            return request.build_absolute_uri(obj.illustration.url) if request else obj.illustration.url
        return None

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


class FeedInterleaveRequestSerializer(serializers.Serializer):
    surface = serializers.CharField(required=False, default='home', allow_blank=True)
    visible_article_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
    )
    cursor = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    limit = serializers.IntegerField(required=False, default=6, min_value=1, max_value=20)
    timezone = serializers.CharField(required=False, default='Africa/Kampala', allow_blank=True)


class EntityTopArticleSerializer(CleanArticleTextRepresentationMixin, serializers.ModelSerializer):
    source = serializers.CharField(source='source.name', read_only=True)
    mention_date = serializers.SerializerMethodField()

    class Meta:
        model = Article
        fields = [
            'id',
            'title',
            'slug',
            'excerpt',
            'source',
            'featured_image_url',
            'published_at',
            'mention_date',
        ]

    def get_mention_date(self, obj):
        mention_date = getattr(obj, '_entity_mention_date', None)
        return mention_date.isoformat() if mention_date else None


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
        return clean_article_text(article.title, preserve_paragraphs=False) if article else None

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
    """
    Story Card: title + summary + overview + image + category + timestamps
    + source count.
    """
    article_count = serializers.IntegerField(read_only=True)
    source_count = serializers.IntegerField(read_only=True)
    card_image_url = serializers.SerializerMethodField()

    class Meta:
        model = StoryCluster
        fields = [
            'id', 'title', 'slug', 'summary', 'why_this_matters',
            'local_impact', 'primary_theme', 'status', 'importance_score',
            'first_seen_at', 'last_seen_at', 'article_count', 'source_count',
            # Story Card content (semantic story engine)
            'short_summary', 'overview', 'key_highlights', 'card_image_url',
            'version', 'synthesized_at',
        ]

    def get_card_image_url(self, obj):
        """Featured image from the most relevant member article that has one."""
        link = (
            obj.cluster_articles
            .select_related('article')
            .exclude(article__featured_image_url='')
            .order_by('-relevance_score')
            .first()
        )
        return link.article.featured_image_url if link else None


class StoryClusterDetailSerializer(StoryClusterListSerializer):
    articles = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    perspectives = SourcePerspectiveSerializer(source='source_perspectives', many=True, read_only=True)
    versions = serializers.SerializerMethodField()
    related_stories = serializers.SerializerMethodField()

    class Meta(StoryClusterListSerializer.Meta):
        fields = StoryClusterListSerializer.Meta.fields + [
            'long_summary', 'articles', 'timeline', 'perspectives', 'versions',
            'related_stories',
        ]

    def get_related_stories(self, obj):
        """Story graph: earlier stories this one continues + later follow-ups."""
        result = []
        for rel in obj.outgoing_relations.select_related('to_cluster'):
            result.append({
                'id': rel.to_cluster.pk,
                'slug': rel.to_cluster.slug,
                'title': rel.to_cluster.title,
                'relation': rel.relation_type,
                'direction': 'earlier',
                'last_seen_at': rel.to_cluster.last_seen_at,
            })
        for rel in obj.incoming_relations.select_related('from_cluster'):
            result.append({
                'id': rel.from_cluster.pk,
                'slug': rel.from_cluster.slug,
                'title': rel.from_cluster.title,
                'relation': rel.relation_type,
                'direction': 'later',
                'last_seen_at': rel.from_cluster.last_seen_at,
            })
        return result

    def get_versions(self, obj):
        return [
            {
                'version': v.version,
                'title': v.title,
                'short_summary': v.short_summary,
                'article_count': v.article_count,
                'change_note': v.change_note,
                'created_at': v.created_at,
            }
            for v in obj.versions.all()[:10]
        ]

    def get_articles(self, obj):
        links = (
            obj.cluster_articles
            .select_related(
                'article__source',
                'article__category',
                'article__author',
                'article__enrichment',
            )
            .filter(article__has_full_content=True)
            .order_by('-relevance_score', '-article__enrichment__importance_score', '-article__published_at')
        )
        result = []
        for link in links:
            article = link.article
            data = ArticleSnippetSerializer(article).data
            enrichment = getattr(article, 'enrichment', None)
            if enrichment and enrichment.status == 'completed':
                data['summary'] = enrichment.summary
                data['importance_score'] = enrichment.importance_score
                data['story_arcs'] = enrichment.related_themes or []
                data['key_facts'] = enrichment.key_facts[:2] if enrichment.key_facts else []
            data['relevance_score'] = link.relevance_score
            result.append(data)
        return result

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
