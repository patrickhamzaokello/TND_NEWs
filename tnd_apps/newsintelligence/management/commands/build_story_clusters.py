from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from tnd_apps.newsintelligence.models import (
    ArticleEnrichment,
    SourcePerspective,
    StoryAlert,
    StoryCluster,
    StoryClusterArticle,
    StoryTimelineEvent,
)


class Command(BaseCommand):
    help = 'Build lightweight story clusters from enriched article themes and related story threads.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--min-articles', type=int, default=2)

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options['days'])
        enrichments = ArticleEnrichment.objects.filter(
            status='completed',
            article__published_at__gte=since,
        ).select_related('article', 'article__source')

        buckets = {}
        for enrichment in enrichments:
            keys = enrichment.related_themes or enrichment.themes or []
            if not keys:
                keys = [enrichment.article.category.name if enrichment.article.category else 'general']
            for key in keys[:3]:
                normalized = slugify(key)[:120] or 'general'
                buckets.setdefault(normalized, {'name': key, 'items': []})['items'].append(enrichment)

        created_or_updated = 0
        for key, bucket in buckets.items():
            items = bucket['items']
            if len(items) < options['min_articles']:
                continue

            top = sorted(items, key=lambda item: item.importance_score or 0, reverse=True)[0]
            cluster, _ = StoryCluster.objects.update_or_create(
                slug=key,
                defaults={
                    'title': bucket['name'].title(),
                    'summary': top.summary,
                    'why_this_matters': top.local_impact.get('impact_note', '') if isinstance(top.local_impact, dict) else '',
                    'local_impact': top.local_impact or {},
                    'primary_theme': (top.themes or [''])[0],
                    'importance_score': top.importance_score or 0,
                    'last_seen_at': max(item.article.published_at or item.article.scraped_at for item in items),
                    'status': 'active',
                },
            )

            for item in items:
                StoryClusterArticle.objects.get_or_create(
                    cluster=cluster,
                    article=item.article,
                    defaults={'relevance_score': min(1.0, (item.importance_score or 5) / 10)},
                )
                SourcePerspective.objects.get_or_create(
                    cluster=cluster,
                    source=item.article.source,
                    article=item.article,
                    defaults={
                        'framing_summary': item.summary,
                        'notable_emphasis': item.themes,
                        'sentiment_score': item.sentiment_score,
                    },
                )
                StoryTimelineEvent.objects.get_or_create(
                    cluster=cluster,
                    article=item.article,
                    title=item.article.title[:240],
                    defaults={
                        'event_date': item.article.published_at or item.article.scraped_at,
                        'description': item.summary,
                        'citations': item.citations,
                    },
                )
                if (item.importance_score or 0) >= 8 or item.is_breaking_candidate:
                    StoryAlert.objects.get_or_create(
                        cluster=cluster,
                        article=item.article,
                        defaults={
                            'title': item.article.title[:240],
                            'reason': item.local_impact.get('impact_note', item.summary)
                            if isinstance(item.local_impact, dict)
                            else item.summary,
                            'importance_score': item.importance_score or 0,
                        },
                    )

            created_or_updated += 1

        self.stdout.write(self.style.SUCCESS(f'Built/updated {created_or_updated} story clusters.'))
