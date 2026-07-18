"""
One-off cleanup: detect story clusters whose member articles span a date gap
wider than the event-detection window (a sign an old/backfilled article got
mis-attached to an unrelated but topically-similar active story before the
temporal-proximity guard in story_engine.find_matching_story existed), and
split them back into separate stories.

Usage:
    python manage.py split_contaminated_stories --dry-run
    python manage.py split_contaminated_stories
    python manage.py split_contaminated_stories --gap-days 21
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from tnd_apps.newsintelligence.models import (
    SourcePerspective, StoryCluster, StoryTimelineEvent,
)
from tnd_apps.newsintelligence.story_engine import _unique_slug, mean_vector


class Command(BaseCommand):
    help = (
        "Detect and split story clusters whose member articles span a date gap "
        "wider than the event window (likely mis-clustered by an old/backfilled article)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--gap-days', type=int, default=14,
            help='Gap in days between consecutive articles (by published date) that triggers a split.',
        )
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--min-articles', type=int, default=2)

    def handle(self, *args, **options):
        gap_days = options['gap_days']
        dry_run = options['dry_run']
        min_articles = options['min_articles']

        inspected = split_count = new_clusters_count = 0

        for cluster in StoryCluster.objects.all().order_by('id').iterator():
            links = list(
                cluster.cluster_articles
                .select_related('article', 'article__enrichment')
            )

            dated = []
            for link in links:
                event_date = link.article.published_at or link.article.scraped_at
                if event_date is not None:
                    dated.append((event_date, link))

            if len(dated) < min_articles:
                continue
            inspected += 1
            dated.sort(key=lambda pair: pair[0])

            groups = [[dated[0]]]
            for date_, link in dated[1:]:
                prev_date = groups[-1][-1][0]
                if (date_ - prev_date).days > gap_days:
                    groups.append([])
                groups[-1].append((date_, link))

            if len(groups) <= 1:
                continue

            split_count += 1
            self.stdout.write(self.style.WARNING(
                f'Cluster {cluster.id} "{cluster.title[:60]}" spans {len(groups)} disjoint time '
                f'groups ({dated[0][0].date()} .. {dated[-1][0].date()})'
            ))
            for i, group in enumerate(groups, start=1):
                self.stdout.write(
                    f'  group {i}: {len(group)} article(s), {group[0][0].date()} .. {group[-1][0].date()}'
                )

            if dry_run:
                continue

            with transaction.atomic():
                # Keep the largest group under the original cluster; split the rest off.
                keep_idx = max(range(len(groups)), key=lambda i: len(groups[i]))
                for i, group in enumerate(groups):
                    if i == keep_idx:
                        continue
                    new_clusters_count += 1
                    self._split_off(cluster, group)
                self._recompute_cluster(cluster, groups[keep_idx])

        action = 'Would split' if dry_run else 'Split'
        self.stdout.write(self.style.SUCCESS(
            f'Inspected {inspected} clusters with dated articles. '
            f'Found {split_count} contaminated (gap > {gap_days}d). '
            f'{action} off {new_clusters_count} new stor(y/ies).'
        ))

    def _split_off(self, original_cluster, group):
        """Move `group`'s articles into a brand-new StoryCluster."""
        first_date, first_link = group[0]
        first_article = first_link.article
        first_enrichment = getattr(first_article, 'enrichment', None)

        title_seed = (
            first_enrichment.neutral_title if first_enrichment and first_enrichment.neutral_title
            else first_article.title
        )
        slug = _unique_slug(slugify(title_seed)[:100])
        dates = [d for d, _ in group]

        new_cluster = StoryCluster.objects.create(
            title=title_seed[:300],
            slug=slug,
            primary_theme=original_cluster.primary_theme,
            first_seen_at=min(dates),
            last_seen_at=max(dates),
            status='active',
        )

        embeddings = []
        importance = 0
        for _, link in group:
            link.cluster = new_cluster
            link.save(update_fields=['cluster'])

            article = link.article
            enrichment = getattr(article, 'enrichment', None)
            if enrichment and enrichment.embedding:
                embeddings.append(enrichment.embedding)
            if enrichment and (enrichment.importance_score or 0) > importance:
                importance = enrichment.importance_score

            SourcePerspective.objects.filter(
                cluster=original_cluster, article=article
            ).update(cluster=new_cluster)
            StoryTimelineEvent.objects.filter(
                cluster=original_cluster, article=article
            ).update(cluster=new_cluster)

        if embeddings:
            new_cluster.centroid_embedding = mean_vector(embeddings)

        # Populate card fields from the first article's enrichment (no LLM call).
        # If 2+ articles ended up here, the next story-engine pass will properly
        # synthesize it since version=0 triggers first synthesis.
        if first_enrichment:
            single_citation = [{
                'article_id': first_article.id,
                'source': first_article.source.name if first_article.source else 'Unknown source',
                'url': first_article.url,
            }]
            new_cluster.summary = first_enrichment.summary or ''
            new_cluster.short_summary = first_enrichment.summary or ''
            new_cluster.why_this_matters = first_enrichment.why_it_matters or ''
            new_cluster.key_highlights = [
                {'text': f, 'sources_count': 1, 'citations': single_citation}
                for f in (first_enrichment.key_facts or [])[:6] if f
            ]
        new_cluster.importance_score = importance
        new_cluster.save()

        self.stdout.write(f'    -> new story {new_cluster.id} "{new_cluster.title[:60]}"')

    def _recompute_cluster(self, cluster, kept_group):
        dates = [d for d, _ in kept_group]
        cluster.first_seen_at = min(dates)
        cluster.last_seen_at = max(dates)
        # Force full re-synthesis on the next story-engine pass since the
        # member set changed.
        cluster.version = 0
        cluster.articles_at_synthesis = 0

        embeddings = []
        for _, link in kept_group:
            enrichment = getattr(link.article, 'enrichment', None)
            if enrichment and enrichment.embedding:
                embeddings.append(enrichment.embedding)
        if embeddings:
            cluster.centroid_embedding = mean_vector(embeddings)

        cluster.save(update_fields=[
            'first_seen_at', 'last_seen_at', 'centroid_embedding',
            'version', 'articles_at_synthesis', 'updated_at',
        ])
