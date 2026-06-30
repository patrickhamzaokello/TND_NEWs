# signals.py
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Article

logger = logging.getLogger(__name__)


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


@receiver(post_save, sender=Article)
def broadcast_new_article(sender, instance, created, update_fields=None, **kwargs):
    """
    Push every newly scraped article (and the moment it gains full content)
    onto the real-time WebSocket stream via the Channels Redis layer.
    """
    # Only stream once the article has real content — avoids pushing
    # placeholder/partial rows that scrapers create before enrichment.
    if not instance.has_full_content:
        return

    becoming_complete = update_fields is None or 'has_full_content' in update_fields
    if not created and not becoming_complete:
        return

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
        # Streaming must never break the scraping pipeline.
        logger.exception("Failed to broadcast article %s to real-time stream", instance.id)


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
