# signals.py
import logging

from django.contrib.postgres.search import SearchVector
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Article

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Article)
def update_search_vector(sender, instance, **kwargs):
    """
    Keep search_vector in sync every time an article is saved.
    Uses update() on a single-row queryset to avoid triggering post_save again.
    Only runs when the article has content worth indexing.
    """
    if not (instance.title or instance.excerpt or instance.content):
        return
    try:
        Article.objects.filter(pk=instance.pk).update(
            search_vector=(
                SearchVector('title', weight='A') +
                SearchVector('excerpt', weight='B') +
                SearchVector('content', weight='C')
            )
        )
    except Exception:
        logger.exception("Failed to update search_vector for article %s", instance.pk)


def _build_stream_payload(article: Article) -> dict:
    return {
        'id': article.id,
        'title': article.title,
        'slug': article.slug,
        'excerpt': article.excerpt,
        'featured_image_url': article.featured_image_url,
        'url': article.url,
        'source': {
            'id': article.source_id,
            'name': article.source.name if article.source else '',
            'favicon_url': article.source.favicon_url if article.source else '',
        },
        'category': {
            'id': article.category_id,
            'name': article.category.name if article.category else None,
            'slug': article.category.slug if article.category else None,
        } if article.category_id else None,
        'published_at': article.published_at.isoformat() if article.published_at else None,
        'scraped_at': article.scraped_at.isoformat() if article.scraped_at else None,
        'read_time_minutes': article.read_time_minutes,
        'has_full_content': article.has_full_content,
    }


def _should_broadcast(instance, created: bool, update_fields) -> bool:
    """
    Return True only when an article newly becomes complete.

    Cases that qualify:
      - Brand-new article saved with has_full_content=True (scraper fetched
        the detail page in the same pass).
      - Existing article updated and has_full_content was explicitly included
        in the set of changed fields (enrichment / retry pass just completed).

    Cases that must NOT broadcast:
      - Re-saves of already-complete articles (e.g. tag M2M changes, minor
        field corrections) where update_fields is None but the article was
        already broadcast — caught by the Redis dedup key below.
      - Any save where has_full_content is still False.
    """
    if not instance.has_full_content:
        return False
    if created:
        return True
    # Explicit update that set has_full_content
    if update_fields is not None and 'has_full_content' in update_fields:
        return True
    # Full save (update_fields=None) on an existing article — this fires on
    # tag changes, minor edits, etc. The Redis dedup key handles the
    # exactly-once guarantee for these cases; let them through here.
    if update_fields is None:
        return True
    return False


@receiver(post_save, sender=Article)
def broadcast_new_article(sender, instance, created, update_fields=None, **kwargs):
    """
    Push a newly-complete article onto the real-time WebSocket stream.

    Exactly-once guarantee is enforced by a Redis key
    ``ws_broadcast:article:<pk>`` (24-hour TTL).  ``cache.add()`` maps to
    Redis SETNX — it returns True only when the key is freshly created, so
    only the first qualifying save wins the broadcast right even if multiple
    Celery workers or signal firings race simultaneously.
    """
    if not _should_broadcast(instance, created, update_fields):
        return

    # ── Atomic exactly-once guard ────────────────────────────────────────
    try:
        from django.core.cache import cache
        dedup_key = f"ws_broadcast:article:{instance.pk}"
        # cache.add() is SETNX: returns True only if the key did not exist.
        claimed = cache.add(dedup_key, "1", timeout=86400)  # 24 h
        if not claimed:
            return  # Another save already broadcast this article
    except Exception:
        # If Redis is down we broadcast anyway — a rare duplicate is better
        # than missing articles entirely.
        logger.warning("Redis dedup unavailable for article %s; broadcasting anyway", instance.pk)

    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        from .consumers import FIREHOSE_GROUP, category_group, source_group

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return

        payload = _build_stream_payload(instance)
        event = {'type': 'article_new', 'article': payload}

        async_to_sync(channel_layer.group_send)(FIREHOSE_GROUP, event)
        if instance.category_id and instance.category:
            async_to_sync(channel_layer.group_send)(category_group(instance.category.slug), event)
        if instance.source_id:
            async_to_sync(channel_layer.group_send)(source_group(instance.source_id), event)

    except Exception:
        logger.exception("Failed to broadcast article %s to real-time stream", instance.pk)


@receiver(post_save, sender=Article)
def detect_breaking_news(sender, instance, created, **kwargs):
    """Automatically detect potential breaking news"""
    if not created:
        return
    
    # Expanded category checks
    breaking_categories = [
        'breaking', 'urgent', 'alert', 'latest', 'developing',
        'live', 'just in', 'update', 'flash', 'bulletin'
    ]
    
    category_match = (
        instance.category and 
        instance.category.name.lower() in breaking_categories
    )
    
    # Expanded title pattern checks
    breaking_prefixes = [
        'breaking:', 'urgent:', 'alert:', 'developing:', 
        'just in:', 'live:', 'update:', 'flash:', 'exclusive:'
    ]
    
    breaking_keywords = [
        'breaking', 'urgent', 'just in', 'developing story',
        'live update', 'flash', 'alert', 'exclusive', 'confirmed'
    ]
    
    title_lower = instance.title.lower()
    
    has_breaking_prefix = any(
        title_lower.startswith(prefix) for prefix in breaking_prefixes
    )
    
    has_breaking_keyword = any(
        keyword in title_lower for keyword in breaking_keywords
    )
    
    # Determine if breaking news
    is_breaking = (
        category_match or
        has_breaking_prefix or
        has_breaking_keyword
    )
    
    if is_breaking:
        from .models import BreakingNews
        
        # Assign priority based on signals
        if has_breaking_prefix or (category_match and 'breaking' in instance.category.name.lower()):
            priority = 'high'
        elif category_match or has_breaking_keyword:
            priority = 'medium'
        else:
            priority = 'low'
        
        BreakingNews.objects.create(
            article=instance,
            priority=priority
        )

        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            from .consumers import FIREHOSE_GROUP

            channel_layer = get_channel_layer()
            if channel_layer is not None:
                async_to_sync(channel_layer.group_send)(FIREHOSE_GROUP, {
                    'type': 'breaking_news',
                    'article': _build_stream_payload(instance),
                    'priority': priority,
                })
        except Exception:
            logger.exception("Failed to broadcast breaking news %s to real-time stream", instance.id)
