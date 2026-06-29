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


def _entity_set(enrichment) -> set:
    """Normalized (type, name) pairs for Jaccard overlap."""
    entities = set()
    for name in (enrichment.entities_people or []):
        n = name.lower().strip()
        if n:
            entities.add(('person', n))
    for name in (enrichment.entities_organizations or []):
        n = name.lower().strip()
        if n:
            entities.add(('org', n))
    for name in (enrichment.entities_locations or []):
        n = name.lower().strip()
        if n:
            entities.add(('loc', n))
    return entities


def _theme_set(enrichment) -> set:
    themes = set()
    for t in (enrichment.themes or []):
        themes.add(t.lower().strip())
    for t in (enrichment.related_themes or []):
        themes.add(t.lower().strip())
    return themes


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _similarity(enrichment, state: dict) -> float:
    """Entity overlap (weight 0.7) + theme overlap (weight 0.3)."""
    entity_sim = _jaccard(_entity_set(enrichment), state['entities'])
    theme_sim = _jaccard(_theme_set(enrichment), state['themes'])
    return 0.7 * entity_sim + 0.3 * theme_sim


def _unique_slug(base: str, existing: set) -> str:
    slug = base
    counter = 1
    while slug in existing:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


class Command(BaseCommand):
    help = 'Build story clusters using entity and theme overlap similarity.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--min-articles', type=int, default=2)
        parser.add_argument(
            '--threshold', type=float, default=0.15,
            help='Min similarity score (0-1) to join an existing cluster',
        )
        parser.add_argument(
            '--dormant-days', type=int, default=3,
            help='Mark cluster dormant if no new articles in this many days',
        )

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options['days'])
        threshold = options['threshold']
        dormant_cutoff = timezone.now() - timedelta(days=options['dormant_days'])

        enrichments = list(
            ArticleEnrichment.objects.filter(
                status='completed',
                article__published_at__gte=since,
            ).select_related('article', 'article__source', 'article__category')
            .order_by('article__published_at')  # oldest first so clusters build up
        )

        self.stdout.write(f'Processing {len(enrichments)} enriched articles...')

        # Seed cluster states from existing active clusters so new articles
        # can join them rather than spawning duplicates.
        cluster_states = {}
        for cluster in StoryCluster.objects.filter(status='active', last_seen_at__gte=since):
            existing_enrichments = list(
                ArticleEnrichment.objects.filter(
                    article__story_cluster_links__cluster=cluster,
                    status='completed',
                ).select_related('article')
            )
            if existing_enrichments:
                cluster_states[cluster.slug] = {
                    'cluster': cluster,
                    'entities': set().union(*[_entity_set(e) for e in existing_enrichments]),
                    'themes': set().union(*[_theme_set(e) for e in existing_enrichments]),
                    'items': existing_enrichments,
                    'is_new': False,
                    'primary_theme': cluster.primary_theme,
                }

        used_slugs = set(cluster_states.keys())

        # Assign each article to best-matching cluster or start a new one.
        for enrichment in enrichments:
            article = enrichment.article
            if not (article.published_at or article.scraped_at):
                continue

            e_entities = _entity_set(enrichment)
            e_themes = _theme_set(enrichment)

            if not e_entities and not e_themes:
                continue

            # Skip if already assigned to a cluster in this state snapshot
            already_assigned = any(
                enrichment in state['items'] for state in cluster_states.values()
            )
            if already_assigned:
                continue

            best_slug = None
            best_score = 0.0
            for slug, state in cluster_states.items():
                score = _similarity(enrichment, state)
                if score > best_score:
                    best_score = score
                    best_slug = slug

            if best_slug and best_score >= threshold:
                state = cluster_states[best_slug]
                state['entities'] |= e_entities
                state['themes'] |= e_themes
                state['items'].append(enrichment)
            else:
                primary_theme = (
                    enrichment.themes
                    or enrichment.related_themes
                    or [article.category.name if article.category else 'general']
                )[0]
                slug_base = slugify(primary_theme)[:100] or 'general'
                slug = _unique_slug(slug_base, used_slugs)
                used_slugs.add(slug)
                cluster_states[slug] = {
                    'cluster': None,
                    'entities': e_entities,
                    'themes': e_themes,
                    'items': [enrichment],
                    'is_new': True,
                    'primary_theme': primary_theme,
                    'slug': slug,
                }

        # Persist clusters that meet the minimum article count.
        created_or_updated = 0
        for slug, state in cluster_states.items():
            items = state['items']
            if len(items) < options['min_articles']:
                continue

            top = max(items, key=lambda e: e.importance_score or 0)
            last_seen = max(
                (item.article.published_at or item.article.scraped_at)
                for item in items
                if (item.article.published_at or item.article.scraped_at)
            )

            if state['cluster']:
                cluster = state['cluster']
                cluster.importance_score = top.importance_score or 0
                cluster.last_seen_at = last_seen
                cluster.status = 'active'
                cluster.save(update_fields=['importance_score', 'last_seen_at', 'status', 'updated_at'])
            else:
                primary_theme = state['primary_theme']
                cluster, _ = StoryCluster.objects.update_or_create(
                    slug=slug,
                    defaults={
                        'title': primary_theme.title(),
                        'summary': top.summary,
                        'why_this_matters': (
                            top.local_impact.get('impact_note', '')
                            if isinstance(top.local_impact, dict) else ''
                        ),
                        'local_impact': top.local_impact or {},
                        'primary_theme': primary_theme,
                        'importance_score': top.importance_score or 0,
                        'last_seen_at': last_seen,
                        'status': 'active',
                    },
                )
                state['cluster'] = cluster

            for item in items:
                relevance = max(0.1, _similarity(item, state))

                StoryClusterArticle.objects.update_or_create(
                    cluster=cluster,
                    article=item.article,
                    defaults={'relevance_score': relevance},
                )
                if item.article.source:
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
                            'reason': (
                                item.local_impact.get('impact_note', item.summary)
                                if isinstance(item.local_impact, dict) else item.summary
                            ),
                            'importance_score': item.importance_score or 0,
                        },
                    )

            created_or_updated += 1

        dormant_count = StoryCluster.objects.filter(
            status='active',
            last_seen_at__lt=dormant_cutoff,
        ).update(status='dormant')

        self.stdout.write(self.style.SUCCESS(
            f'Built/updated {created_or_updated} story clusters. '
            f'Marked {dormant_count} dormant.'
        ))
