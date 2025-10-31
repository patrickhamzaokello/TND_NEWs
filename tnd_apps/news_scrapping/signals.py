# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Article

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
