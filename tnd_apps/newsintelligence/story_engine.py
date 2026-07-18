"""
Semantic story engine — Particle-style story clustering.

Pipeline (per article):
  1. EMBED      — vector representation of title + summary + entities
  2. DETECT     — nearest active story by cosine similarity + entity/time validation
  3. ASSIGN     — attach to existing story OR create a new one
  4. SYNTHESIZE — LLM generates the story's canonical title/summaries/highlights
  5. VERSION    — every significant re-synthesis snapshots a StoryVersion

Embedding model : text-embedding-3-small (1536 dims, $0.02/1M tokens)
Synthesis model : DIGEST_MODEL (default gpt-4o-mini)

Vector search is done in-process (cosine over active cluster centroids).
At this platform's volume (~50-200 articles/day, <300 active stories) that is
faster than a round-trip to a vector DB and requires no new infrastructure.
"""

import logging
import math
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = 'text-embedding-3-small'
EMBEDDING_DIMS = 1536

# ── Event detection thresholds ────────────────────────────────────────────────
SEMANTIC_ATTACH_THRESHOLD = 0.66   # combined score needed to join a story
COSINE_STRONG_MATCH = 0.86         # cosine alone above this ⇒ same event
COSINE_FLOOR = 0.52                # below this, never attach regardless of entities
MIN_ENTITY_OVERLAP = 0.04          # non-strong matches MUST share at least one entity
EVENT_WINDOW_DAYS = 14             # active-story matching window
COSINE_WEIGHT = 0.70
ENTITY_WEIGHT = 0.30

# ── Story revival (old stories resurfacing: court rulings, verdicts, follow-ups)
REVIVAL_LOOKBACK_DAYS = 365        # how far back to search for a dormant parent story
REVIVAL_COSINE_DIRECT = 0.80       # cosine + entity overlap ⇒ revive without asking LLM
REVIVAL_MIN_ENTITY = 0.10          # minimum entity overlap for direct revival
ADJUDICATION_COSINE_MIN = 0.62     # borderline band: ask the LLM to decide
ADJUDICATION_MAX_CANDIDATES = 2    # at most this many LLM adjudication calls per article

# ── Synthesis triggers ────────────────────────────────────────────────────────
# Single-article stories get their card fields directly from the article's
# enrichment (no extra LLM call); full synthesis starts at 2+ sources where
# there is actually something to synthesize.
SYNTHESIS_MIN_ARTICLES = 2
# Re-synthesize on every new article once a story has 2+ sources. Long-running
# stories (e.g. a multi-week sports campaign) must never show a stale title/
# summary from an earlier chapter while last_seen_at ticks forward from an
# unrelated later article — staleness is worse than the extra cheap LLM call.
SYNTHESIS_GROWTH_TRIGGER = 1
SYNTHESIS_IMPORTANCE_TRIGGER = 7   # kept as a fallback trigger path, still cheap to check


# ══════════════════════════════════════════════════════════════════════════════
# 1. EMBEDDING
# ══════════════════════════════════════════════════════════════════════════════

def _embedding_text(enrichment) -> str:
    """
    Build the text that gets embedded: headline + article body + entities.
    Article.content is cleaned at save time (clean_article_text), so the body
    is safe to embed directly. Body is capped to stay within embedding token
    limits (~6000 chars ≈ 1500 tokens); the opening of a news article carries
    the event's who/what/where, so truncation loses little signal.
    """
    article = enrichment.article
    parts = [article.title or '']

    body = (article.content or '').strip()
    if body:
        parts.append(body[:6000])
    elif enrichment.summary:
        # Fall back to the AI summary when full content wasn't scraped
        parts.append(enrichment.summary)

    entities = (
        (enrichment.entities_people or [])
        + (enrichment.entities_organizations or [])
        + (enrichment.entities_locations or [])
    )
    if entities:
        parts.append('Entities: ' + ', '.join(entities[:20]))
    if enrichment.themes:
        parts.append('Topics: ' + ', '.join(enrichment.themes))
    return '\n'.join(parts)[:8000]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Call OpenAI embeddings API for a batch of texts."""
    from .openai_client import _get_client
    client = _get_client()
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def generate_article_embedding(enrichment, save: bool = True) -> list[float]:
    """Generate and store the embedding for one enrichment."""
    vector = embed_texts([_embedding_text(enrichment)])[0]
    if save:
        enrichment.embedding = vector
        enrichment.embedded_at = timezone.now()
        enrichment.save(update_fields=['embedding', 'embedded_at'])
    return vector


def embed_pending_articles(batch_size: int = 100) -> int:
    """Embed all completed enrichments that don't have an embedding yet."""
    from .models import ArticleEnrichment

    pending = list(
        ArticleEnrichment.objects.filter(
            status='completed', embedding__isnull=True,
        ).select_related('article')[:batch_size]
    )
    if not pending:
        return 0

    texts = [_embedding_text(e) for e in pending]
    vectors = embed_texts(texts)
    now = timezone.now()
    for enrichment, vector in zip(pending, vectors):
        enrichment.embedding = vector
        enrichment.embedded_at = now
        enrichment.save(update_fields=['embedding', 'embedded_at'])

    logger.info('Embedded %d articles', len(pending))
    return len(pending)


# ══════════════════════════════════════════════════════════════════════════════
# Vector math (pure python — no numpy dependency needed at this volume)
# ══════════════════════════════════════════════════════════════════════════════

def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(len(vectors[0]))]


# ══════════════════════════════════════════════════════════════════════════════
# 2 + 4. EVENT DETECTION & ASSIGNMENT
# ══════════════════════════════════════════════════════════════════════════════

def _entity_set(enrichment) -> set:
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


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cluster_entity_set(cluster) -> set:
    """Union of entity sets of the cluster's most recent member articles."""
    from .models import ArticleEnrichment
    members = ArticleEnrichment.objects.filter(
        article__story_cluster_links__cluster=cluster, status='completed',
    ).order_by('-article__published_at')[:10]
    result = set()
    for m in members:
        result |= _entity_set(m)
    return result


def find_matching_story(enrichment):
    """
    Event detection: find the best active story for an article.

    Returns (cluster, combined_score) or (None, best_score_seen).
    """
    from .models import StoryCluster

    vector = enrichment.embedding
    if not vector:
        return None, 0.0

    window_start = timezone.now() - timedelta(days=EVENT_WINDOW_DAYS)
    candidates = StoryCluster.objects.filter(
        status='active',
        last_seen_at__gte=window_start,
        centroid_embedding__isnull=False,
    )

    # The article's OWN publish date, not the scrape time. A backfilled or
    # late-scraped article about an old event must not silently attach to an
    # unrelated but topically-similar ACTIVE story just because that story
    # happens to be currently active — the two events may be weeks apart.
    article = enrichment.article
    article_date = article.published_at

    best_cluster = None
    best_score = 0.0

    for cluster in candidates:
        cos = cosine_similarity(vector, cluster.centroid_embedding)
        if cos < COSINE_FLOOR:
            continue

        # Temporal proximity gate: this article's real event date must fall
        # within the active-story window relative to the story's own timeline
        # (not just "cluster is active right now"). Skip clusters the article
        # couldn't plausibly belong to on a timeline basis. If the article has
        # no confirmed publish date, treat it as unproven — only strong direct
        # matches proceed, everything else defers to stage-2 adjudication.
        if article_date is not None:
            days_apart = abs((article_date - cluster.last_seen_at).days)
            if days_apart > EVENT_WINDOW_DAYS:
                continue
        elif cos < COSINE_STRONG_MATCH:
            continue

        # Strong semantic match — same event, no further validation needed
        if cos >= COSINE_STRONG_MATCH:
            return cluster, cos

        # Otherwise validate with entity overlap. An article that shares NO
        # named entity with the story can never join it — this prevents
        # thematically-similar-but-unrelated events (two different sports
        # stories, two different corruption cases) from merging.
        entity_sim = _jaccard(_entity_set(enrichment), _cluster_entity_set(cluster))
        if entity_sim < MIN_ENTITY_OVERLAP:
            continue

        combined = COSINE_WEIGHT * cos + ENTITY_WEIGHT * entity_sim

        if combined > best_score:
            best_score = combined
            best_cluster = cluster

    if best_cluster and best_score >= SEMANTIC_ATTACH_THRESHOLD:
        return best_cluster, best_score
    return None, best_score


# ── Story revival & LLM adjudication ─────────────────────────────────────────

def _adjudicate(enrichment, cluster) -> str:
    """
    Ask the LLM whether a new article belongs to an older story.
    Returns 'same_story', 'related_story', or 'unrelated'.
    """
    from .openai_client import call_openai, parse_json_response
    from .prompts import STORY_ADJUDICATION_SYSTEM, STORY_ADJUDICATION_USER

    article = enrichment.article
    entities = (
        (enrichment.entities_people or [])
        + (enrichment.entities_organizations or [])
        + (enrichment.entities_locations or [])
    )
    highlights = [h.get('text', '') for h in (cluster.key_highlights or [])][:5]

    user_prompt = STORY_ADJUDICATION_USER.format(
        article_title=article.title,
        article_summary=enrichment.summary or '',
        article_entities=', '.join(entities[:15]) or '(none)',
        article_date=str(article.published_at or article.scraped_at or ''),
        story_last_seen=str(cluster.last_seen_at.date()),
        story_title=cluster.title,
        story_summary=cluster.short_summary or cluster.summary or '',
        story_highlights='; '.join(highlights) or '(none)',
    )

    try:
        response = call_openai(
            system=STORY_ADJUDICATION_SYSTEM,
            user=user_prompt,
            max_tokens=150,
        )
        data = parse_json_response(response.content)
        relationship = data.get('relationship', 'unrelated')
        logger.info(
            'Adjudication: article %d vs story %d → %s (%s)',
            article.pk, cluster.pk, relationship, data.get('reason', '')[:100],
        )
        if relationship in ('same_story', 'related_story', 'unrelated'):
            return relationship
    except Exception as exc:
        logger.warning('Adjudication failed for article %d: %s', article.pk, exc)
    return 'unrelated'


def find_revival_story(enrichment):
    """
    Stage-2 event detection: search OLDER stories (dormant or outside the active
    window) for a parent — e.g. a court ruling months after the arrest story.

    Returns (cluster, action) where action is:
      'attach'  — same continuing story: revive it and attach the article
      'related' — distinct event: create a new story but link it to this one
      (None, None) if no candidate qualifies.
    """
    from .models import StoryCluster

    vector = enrichment.embedding
    if not vector:
        return None, None

    window_start = timezone.now() - timedelta(days=EVENT_WINDOW_DAYS)
    lookback_start = timezone.now() - timedelta(days=REVIVAL_LOOKBACK_DAYS)

    # Stories NOT covered by stage 1: dormant/resolved, or active but stale
    candidates = StoryCluster.objects.filter(
        last_seen_at__gte=lookback_start,
        last_seen_at__lt=window_start,
        centroid_embedding__isnull=False,
    )

    article_entities = _entity_set(enrichment)
    scored = []
    for cluster in candidates:
        cos = cosine_similarity(vector, cluster.centroid_embedding)
        if cos < ADJUDICATION_COSINE_MIN:
            continue
        entity_sim = _jaccard(article_entities, _cluster_entity_set(cluster))
        scored.append((cos, entity_sim, cluster))

    if not scored:
        return None, None

    scored.sort(key=lambda x: x[0], reverse=True)

    # Direct revival: very strong semantic match + shared named entities
    top_cos, top_entity, top_cluster = scored[0]
    if top_cos >= REVIVAL_COSINE_DIRECT and top_entity >= REVIVAL_MIN_ENTITY:
        logger.info(
            'Story revival (direct) | article=%d story=%d cos=%.3f entity=%.3f',
            enrichment.article.pk, top_cluster.pk, top_cos, top_entity,
        )
        return top_cluster, 'attach'

    # Borderline band: let the LLM adjudicate the best candidates
    for cos, entity_sim, cluster in scored[:ADJUDICATION_MAX_CANDIDATES]:
        verdict = _adjudicate(enrichment, cluster)
        if verdict == 'same_story':
            return cluster, 'attach'
        if verdict == 'related_story':
            return cluster, 'related'

    return None, None


def _update_centroid(cluster):
    """Recompute the cluster centroid from member embeddings."""
    from .models import ArticleEnrichment
    vectors = list(
        ArticleEnrichment.objects.filter(
            article__story_cluster_links__cluster=cluster,
            status='completed', embedding__isnull=False,
        ).values_list('embedding', flat=True)[:50]  # cap for cost; recent-enough centroid
    )
    if vectors:
        cluster.centroid_embedding = mean_vector(vectors)
        cluster.save(update_fields=['centroid_embedding'])


def _unique_slug(base: str) -> str:
    from .models import StoryCluster
    slug = base or 'story'
    counter = 1
    candidate = slug
    while StoryCluster.objects.filter(slug=candidate).exists():
        candidate = f'{slug}-{counter}'
        counter += 1
    return candidate


@transaction.atomic
def assign_article_to_story(enrichment) -> tuple:
    """
    Assign one embedded article to a story (existing or new).

    Returns (cluster, created: bool).
    """
    from .models import (
        SourcePerspective, StoryCluster, StoryClusterArticle,
        StoryClusterRelation, StoryTimelineEvent,
    )

    article = enrichment.article
    if not article.published_at:
        # scraped_at is when WE saw it, not when the event happened — using it
        # as event_date makes reprints/retrospectives of old events look like
        # breaking news. Log so bad-date scrapers are visible and fixable.
        logger.warning(
            'Article %d has no published_at — falling back to scraped_at for '
            'story timeline. This can misdate old/reprinted content as fresh. '
            'source=%s url=%s',
            article.pk, article.source.name if article.source else '?', article.url,
        )
    event_date = article.published_at or article.scraped_at or timezone.now()

    # Already assigned? Skip.
    existing_link = StoryClusterArticle.objects.filter(article=article).first()
    if existing_link:
        return existing_link.cluster, False

    # Stage 1: match against active stories in the event window
    cluster, score = find_matching_story(enrichment)
    created = False
    related_parent = None  # older story to link via story graph

    # Stage 2: no active match — search older stories (revival / follow-ups)
    if cluster is None:
        revival_cluster, action = find_revival_story(enrichment)
        if action == 'attach':
            # Same continuing story: revive it
            cluster = revival_cluster
            score = 0.8
            logger.info(
                'Story revived | article=%d story=%d "%s"',
                article.pk, cluster.pk, cluster.title[:60],
            )
        elif action == 'related':
            # Distinct event but part of an arc: new story, linked to the old one
            related_parent = revival_cluster

    if cluster is None:
        # ── Create a new story ────────────────────────────────────────────────
        # Card fields come straight from the article's enrichment — no LLM call.
        # Full synthesis replaces them once a second source joins.
        arc_name = (enrichment.related_themes or [None])[0]
        title_seed = arc_name or article.title[:120]
        slug = _unique_slug(slugify(title_seed)[:100])

        card_title = (enrichment.neutral_title or article.title)[:300]
        summary = enrichment.summary or ''
        highlights = [
            {'text': fact, 'sources_count': 1}
            for fact in (enrichment.key_facts or [])[:6]
            if isinstance(fact, str) and fact.strip()
        ]
        why = enrichment.why_it_matters or ''
        if not why and isinstance(enrichment.local_impact, dict):
            why = enrichment.local_impact.get('impact_note', '')

        # Entities: only surface forms that appear verbatim in the card text
        # (clients substring-match to render clickable tags)
        rendered = ' '.join([card_title, summary, why] + [h['text'] for h in highlights])
        entities = []
        seen_names = set()
        for name_list, etype in [
            (enrichment.entities_people, 'person'),
            (enrichment.entities_organizations, 'organization'),
            (enrichment.entities_locations, 'location'),
        ]:
            for name in (name_list or []):
                name = (name or '').strip()
                if name and name not in seen_names and name in rendered:
                    seen_names.add(name)
                    entities.append({'name': name, 'type': etype})

        cluster = StoryCluster.objects.create(
            title=card_title,
            slug=slug,
            summary=summary,
            short_summary=summary,
            why_this_matters=why,
            key_highlights=highlights,
            entities=entities,
            primary_theme=(enrichment.themes or ['general'])[0],
            importance_score=enrichment.importance_score or 0,
            first_seen_at=event_date,
            last_seen_at=event_date,
            status='active',
            centroid_embedding=enrichment.embedding,
        )
        created = True
        logger.info('New story created | id=%d "%s"', cluster.pk, cluster.title[:60])

        if related_parent:
            StoryClusterRelation.objects.get_or_create(
                from_cluster=cluster,
                to_cluster=related_parent,
                defaults={
                    'relation_type': 'related',
                    'note': f'Linked by adjudication via article "{article.title[:100]}"',
                },
            )
            logger.info(
                'Story graph link | new story %d → parent story %d',
                cluster.pk, related_parent.pk,
            )
    else:
        logger.info(
            'Article %d joined story %d (score=%.3f) "%s"',
            article.pk, cluster.pk, score, cluster.title[:60],
        )

    # ── Link article + side records ────────────────────────────────────────────
    StoryClusterArticle.objects.create(
        cluster=cluster, article=article,
        relevance_score=max(score, 0.5 if created else score),
    )
    if article.source:
        SourcePerspective.objects.get_or_create(
            cluster=cluster, source=article.source, article=article,
            defaults={
                'framing_summary': enrichment.summary,
                'notable_emphasis': enrichment.themes,
                'sentiment_score': enrichment.sentiment_score,
            },
        )
    StoryTimelineEvent.objects.get_or_create(
        cluster=cluster, article=article, title=article.title[:240],
        defaults={
            'event_date': event_date,
            'description': enrichment.summary,
            'citations': [{'url': article.url, 'title': article.title}],
        },
    )

    # ── Update cluster stats ───────────────────────────────────────────────────
    if event_date > cluster.last_seen_at:
        cluster.last_seen_at = event_date
    cluster.importance_score = max(cluster.importance_score, enrichment.importance_score or 0)
    cluster.status = 'active'
    cluster.save(update_fields=['last_seen_at', 'importance_score', 'status', 'updated_at'])

    if not created:
        _update_centroid(cluster)

    return cluster, created


# ══════════════════════════════════════════════════════════════════════════════
# 3 + 5. STORY SYNTHESIS & VERSIONING
# ══════════════════════════════════════════════════════════════════════════════

def _needs_synthesis(cluster) -> tuple[bool, str]:
    """Impact scoring: decide whether the story warrants (re-)synthesis."""
    from .models import ArticleEnrichment

    article_count = cluster.cluster_articles.count()
    if article_count < SYNTHESIS_MIN_ARTICLES:
        return False, 'below minimum article count'

    new_articles = article_count - cluster.articles_at_synthesis
    if cluster.version == 0:
        return True, f'first synthesis ({article_count} articles)'
    if new_articles >= SYNTHESIS_GROWTH_TRIGGER:
        return True, f'{new_articles} new articles since v{cluster.version}'

    # High-importance new arrival forces refresh even for a single article
    if new_articles > 0:
        latest = ArticleEnrichment.objects.filter(
            article__story_cluster_links__cluster=cluster, status='completed',
        ).order_by('-article__published_at').first()
        if latest and (latest.importance_score or 0) >= SYNTHESIS_IMPORTANCE_TRIGGER:
            return True, f'high-importance update (score={latest.importance_score})'

    return False, 'no significant change'


def synthesize_story(cluster, force: bool = False) -> bool:
    """
    Generate the story's canonical title, summaries, and key highlights from
    all member articles. Snapshots a StoryVersion on every synthesis.

    Returns True if a new version was produced.
    """
    import json

    from .models import ArticleEnrichment, StoryVersion
    from .openai_client import DIGEST_MODEL, call_openai, parse_json_response
    from .prompts import STORY_SYNTHESIS_SYSTEM, STORY_SYNTHESIS_USER

    if not force:
        needed, reason = _needs_synthesis(cluster)
        if not needed:
            return False
    else:
        reason = 'forced'

    members = list(
        ArticleEnrichment.objects.filter(
            article__story_cluster_links__cluster=cluster, status='completed',
        ).select_related('article', 'article__source')
        .order_by('article__published_at')
    )
    if not members:
        return False

    articles_payload = []
    for m in members:
        articles_payload.append({
            'source': m.article.source.name if m.article.source else 'unknown',
            'published': str(m.article.published_at or m.article.scraped_at or ''),
            'title': m.article.title,
            'summary': m.summary,
            'key_facts': m.key_facts,
            'importance': m.importance_score,
        })

    # Story graph context: earlier related stories feed the overview
    related_lines = []
    for rel in cluster.outgoing_relations.select_related('to_cluster')[:5]:
        parent = rel.to_cluster
        related_lines.append(
            f'- [{parent.last_seen_at.date()}] {parent.title}: {parent.short_summary or parent.summary or ""}'[:300]
        )
    related_stories = '\n'.join(related_lines) or '(none)'

    latest = members[-1]
    latest_summary = (
        f"[{latest.article.source.name if latest.article.source else 'unknown'}] "
        f"{latest.article.title} — {latest.summary}"
    )

    user_prompt = STORY_SYNTHESIS_USER.format(
        article_count=len(members),
        articles_json=json.dumps(articles_payload, ensure_ascii=False, indent=1)[:24000],
        current_title=cluster.title,
        current_summary=cluster.short_summary or '(none yet)',
        related_stories=related_stories,
        latest_article_summary=latest_summary,
    )

    response = call_openai(
        system=STORY_SYNTHESIS_SYSTEM,
        user=user_prompt,
        model=DIGEST_MODEL,
        max_tokens=2000,
    )
    data = parse_json_response(response.content)

    title = (data.get('title') or cluster.title)[:300]
    short_summary = data.get('short_summary', '')
    long_summary = data.get('long_summary', '')
    overview = data.get('overview', '')
    why_it_matters = data.get('why_it_matters', '')
    key_highlights = data.get('key_highlights', [])

    # Validate entities: keep only well-formed entries that actually appear
    # verbatim somewhere in the synthesized text (clients substring-match).
    rendered_text = ' '.join([
        title, short_summary, overview, why_it_matters,
        ' '.join(h.get('text', '') for h in key_highlights if isinstance(h, dict)),
    ])
    entities = []
    seen_names = set()
    for ent in (data.get('entities') or []):
        if not isinstance(ent, dict):
            continue
        name = (ent.get('name') or '').strip()
        etype = ent.get('type', '')
        if not name or etype not in ('person', 'organization', 'location'):
            continue
        if name in seen_names or name not in rendered_text:
            continue
        seen_names.add(name)
        entities.append({'name': name, 'type': etype})

    from django.db.models import Max
    from .models import StoryCluster

    with transaction.atomic():
        # Lock the row and compute the next version from the DB — the in-memory
        # cluster.version may be stale if another worker synthesized concurrently.
        locked = StoryCluster.objects.select_for_update().get(pk=cluster.pk)
        max_existing = StoryVersion.objects.filter(cluster=locked).aggregate(
            m=Max('version')
        )['m'] or 0
        next_version = max(locked.version, max_existing) + 1

        locked.title = title
        locked.short_summary = short_summary
        locked.long_summary = long_summary
        locked.overview = overview
        locked.why_this_matters = why_it_matters or locked.why_this_matters
        locked.key_highlights = key_highlights
        locked.entities = entities
        locked.summary = short_summary  # keep legacy field in sync
        locked.version = next_version
        locked.synthesized_at = timezone.now()
        locked.articles_at_synthesis = len(members)
        locked.save(update_fields=[
            'title', 'short_summary', 'long_summary', 'overview',
            'why_this_matters', 'key_highlights', 'entities', 'summary',
            'version', 'synthesized_at', 'articles_at_synthesis', 'updated_at',
        ])

        StoryVersion.objects.create(
            cluster=locked,
            version=next_version,
            title=title,
            short_summary=short_summary,
            long_summary=long_summary,
            overview=overview,
            key_highlights=key_highlights,
            article_count=len(members),
            change_note=reason[:300],
        )

        # Reflect the new state on the caller's instance
        cluster.version = next_version
        cluster.title = title
        cluster.short_summary = short_summary

    logger.info(
        'Story synthesized | id=%d v%d (%s) "%s"',
        cluster.pk, cluster.version, reason, title[:60],
    )
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline entrypoint
# ══════════════════════════════════════════════════════════════════════════════

def process_new_articles(batch_size: int = 100) -> dict:
    """
    Full pipeline pass:
      1. Embed articles missing embeddings
      2. Assign unassigned embedded articles to stories
      3. Synthesize stories that changed significantly

    Designed to run after each enrichment cycle.
    """
    from .models import ArticleEnrichment, StoryClusterArticle

    embedded = embed_pending_articles(batch_size)

    # Articles with embeddings but no story assignment
    unassigned = list(
        ArticleEnrichment.objects.filter(
            status='completed', embedding__isnull=False,
        ).exclude(
            article__story_cluster_links__isnull=False,
        ).select_related('article', 'article__source')
        .order_by('article__published_at')[:batch_size]
    )

    assigned = created = 0
    touched_clusters = set()
    for enrichment in unassigned:
        try:
            cluster, was_created = assign_article_to_story(enrichment)
            touched_clusters.add(cluster.pk)
            assigned += 1
            if was_created:
                created += 1
        except Exception as exc:
            logger.exception('Story assignment failed for enrichment %d: %s', enrichment.pk, exc)

    # Synthesize changed stories
    synthesized = 0
    from .models import StoryCluster
    for cluster in StoryCluster.objects.filter(pk__in=touched_clusters):
        try:
            if synthesize_story(cluster):
                synthesized += 1
        except Exception as exc:
            logger.exception('Story synthesis failed for cluster %d: %s', cluster.pk, exc)

    # Invalidate story caches when anything changed
    if assigned or synthesized:
        try:
            from tnd_apps.cache_utils import on_clusters_rebuilt
            on_clusters_rebuilt()
        except Exception:
            pass

    result = {
        'embedded': embedded,
        'assigned': assigned,
        'stories_created': created,
        'synthesized': synthesized,
    }
    logger.info('Story engine pass complete | %s', result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# On-demand "Explain Like I'm 5"
# ══════════════════════════════════════════════════════════════════════════════

def get_or_generate_eli5(cluster) -> tuple[str, bool]:
    """
    Return (explanation, was_cached). Generates once per story version and
    caches on the cluster; repeat requests (from any user) get the same text
    until the story is re-synthesized (new articles change the underlying facts).
    """
    from .openai_client import call_openai, parse_json_response
    from .prompts import ELI5_SYSTEM, ELI5_USER

    if cluster.eli5_explanation and cluster.eli5_source_version >= cluster.version:
        return cluster.eli5_explanation, True

    user_prompt = ELI5_USER.format(
        title=cluster.title,
        summary=cluster.short_summary or cluster.summary or '',
        overview=cluster.overview or '',
    )
    response = call_openai(system=ELI5_SYSTEM, user=user_prompt, model='gpt-4o-mini', max_tokens=300)
    data = parse_json_response(response.content)
    explanation = (data.get('explanation') or '').strip()

    cluster.eli5_explanation = explanation
    cluster.eli5_generated_at = timezone.now()
    cluster.eli5_source_version = cluster.version
    cluster.save(update_fields=['eli5_explanation', 'eli5_generated_at', 'eli5_source_version'])

    logger.info('ELI5 generated | story=%d version=%d', cluster.pk, cluster.version)
    return explanation, False
