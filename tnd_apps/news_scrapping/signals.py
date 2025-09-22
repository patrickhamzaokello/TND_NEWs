# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Article

@receiver(post_save, sender=Article)
def detect_breaking_news(sender, instance, created, **kwargs):
    """Automatically detect potential breaking news"""
    if created and instance.category and instance.category.name.lower() in [
        'breaking', 'urgent', 'alert', 'latest'
    ]:
        # Check if this looks like breaking news
        is_breaking = (
            instance.title.lower().startswith(('breaking:', 'urgent:', 'alert:')) or
            'breaking' in instance.title.lower() or
            instance.priority == 'high'
        )
        
        if is_breaking:
            from .models import BreakingNews
            BreakingNews.objects.create(
                article=instance,
                priority='medium'
            )
