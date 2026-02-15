"""
Enrichment agents.

ArticleAnalysisAgent  — processes a single Article → ArticleEnrichment (Silver)
EntityExtractionAgent — flattens entities into EntityMention rows
DailyDigestAgent      — synthesizes a DailyDigest from the day's enrichments (Gold)
"""

import json
import logging
from datetime import date, timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from .openai_client import (
    DIGEST_MODEL,
    ENRICHMENT_MODEL,
    call_openai,
    parse_json_response,
)
from .models import ArticleEnrichment, DailyDigest, EntityMention
from .prompts import (
    ARTICLE_ANALYSIS_SYSTEM,
    ARTICLE_ANALYSIS_USER,
    DAILY_DIGEST_SYSTEM,
    DAILY_DIGEST_USER,
)

logger = logging.getLogger(__name__)

# Max words sent to Claude — saves tokens without losing signal
MAX_CONTENT_WORDS = 450


def _truncate_content(text: str, max_words: int = MAX_CONTENT_WORDS) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return ' '.join(words[:max_words]) + '...'


# ── Agent 1: Article Analysis ─────────────────────────────────────────────────

class ArticleAnalysisAgent:
    """
    Processes a single Article with has_full_content=True.
    Creates or updates its ArticleEnrichment record.
    """

    def process(self, article) -> ArticleEnrichment:
        """
        Main entry point. Pass a news.Article instance.
        Returns the ArticleEnrichment (completed or failed).
        """
        # Get or create the enrichment record
        enrichment, _ = ArticleEnrichment.objects.get_or_create(article=article)

        # Skip if already successfully analyzed
        if enrichment.status == 'completed':
            logger.debug("Article %d already enriched — skipping", article.id)
            return enrichment

        # Mark as in-progress
        enrichment.status = 'processing'
        enrichment.save(update_fields=['status'])

        try:
            result = self._call_llm(article)
            self._save_enrichment(enrichment, result)
            logger.info("✓ Enriched article %d: %s", article.id, article.title[:60])
            return enrichment

        except Exception as e:
            enrichment.status = 'failed'
            enrichment.error_message = str(e)
            enrichment.retry_count += 1
            enrichment.save(update_fields=['status', 'error_message', 'retry_count'])
            logger.error("✗ Failed to enrich article %d: %s", article.id, e)
            raise

    def _call_llm(self, article) -> dict:
        content = _truncate_content(article.content or article.excerpt or '')
        prompt = ARTICLE_ANALYSIS_USER.format(
            title=article.title,
            content=content,
        )
        llm_response = call_openai(
            system=ARTICLE_ANALYSIS_SYSTEM,
            user=prompt,
            model=ENRICHMENT_MODEL,
        )
        parsed = parse_json_response(llm_response.content)
        parsed['_meta'] = {
            'input_tokens':  llm_response.input_tokens,
            'output_tokens': llm_response.output_tokens,
            'model':         llm_response.model,
        }
        return parsed

    def _save_enrichment(self, enrichment: ArticleEnrichment, data: dict):
        meta = data.pop('_meta', {})
        entities = data.get('entities', {})
        audience = data.get('audience_relevance', {})

        enrichment.status           = 'completed'
        enrichment.summary          = data.get('summary', '')
        enrichment.sentiment        = data.get('sentiment', 'neutral')
        enrichment.sentiment_score  = data.get('sentiment_score')
        enrichment.importance_score = data.get('importance_score')
        enrichment.themes           = data.get('themes', [])
        enrichment.key_facts        = data.get('key_facts', [])
        enrichment.related_themes   = data.get('related_themes', [])

        enrichment.entities_people        = entities.get('people', [])
        enrichment.entities_organizations = entities.get('organizations', [])
        enrichment.entities_locations     = entities.get('locations', [])

        enrichment.audience_business   = audience.get('business')
        enrichment.audience_general    = audience.get('general_public')
        enrichment.audience_government = audience.get('government')
        enrichment.audience_youth      = audience.get('youth')

        enrichment.follow_up_worthy      = data.get('follow_up_worthy', False)
        enrichment.controversy_flag      = data.get('controversy_flag', False)
        enrichment.is_breaking_candidate = data.get('is_breaking_candidate', False)

        enrichment.input_tokens_used  = meta.get('input_tokens', 0)
        enrichment.output_tokens_used = meta.get('output_tokens', 0)
        enrichment.model_used         = meta.get('model', '')
        enrichment.analyzed_at        = timezone.now()
        enrichment.error_message      = ''

        enrichment.save()


# ── Agent 2: Entity Extraction ────────────────────────────────────────────────

class EntityExtractionAgent:
    """
    Flattens entities from a completed ArticleEnrichment into
    individual EntityMention rows for trend detection queries.
    """

    def process(self, enrichment: ArticleEnrichment):
        if enrichment.status != 'completed':
            return

        # Remove old mentions for this enrichment (idempotent)
        EntityMention.objects.filter(enrichment=enrichment).delete()

        mention_date = (
            enrichment.article.published_at.date()
            if enrichment.article.published_at
            else timezone.now().date()
        )

        mentions = []
        entity_map = {
            'person':       enrichment.entities_people,
            'organization': enrichment.entities_organizations,
            'location':     enrichment.entities_locations,
        }

        for entity_type, entity_list in entity_map.items():
            for name in entity_list:
                if name and name.strip():
                    mentions.append(EntityMention(
                        enrichment=enrichment,
                        entity_name=name.strip(),
                        entity_type=entity_type,
                        mention_date=mention_date,
                        sentiment_score=enrichment.sentiment_score,
                    ))

        if mentions:
            EntityMention.objects.bulk_create(mentions, ignore_conflicts=True)
            logger.debug(
                "Extracted %d entity mentions for article %d",
                len(mentions), enrichment.article_id
            )


# ── Agent 3: Daily Digest ─────────────────────────────────────────────────────

class DailyDigestAgent:
    """
    Synthesizes a DailyDigest (Gold layer) from the day's enriched articles.
    Should run once daily, typically at 6 AM via Celery/Airflow.
    """

    def generate(self, target_date: Optional[date] = None) -> DailyDigest:
        if target_date is None:
            target_date = timezone.now().date() - timedelta(days=1)  # yesterday

        # Idempotent: get or create
        digest, created = DailyDigest.objects.get_or_create(digest_date=target_date)

        if not created and digest.is_published:
            logger.info("Digest for %s already published — skipping", target_date)
            return digest

        enrichments = self._fetch_enrichments(target_date)
        if not enrichments:
            logger.warning("No enriched articles found for %s", target_date)
            return digest

        trending = self._get_trending_entities(target_date)

        try:
            result = self._call_llm(target_date, enrichments, trending)
            self._save_digest(digest, result, enrichments)
            logger.info("✓ Daily digest generated for %s (%d articles)", target_date, len(enrichments))
        except Exception as e:
            logger.error("✗ Failed to generate digest for %s: %s", target_date, e)
            raise

        return digest

    def _fetch_enrichments(self, target_date: date):
        return list(
            ArticleEnrichment.objects.filter(
                status='completed',
                article__published_at__date=target_date,
            ).select_related('article').order_by('-importance_score')[:50]
        )

    def _get_trending_entities(self, target_date: date, window_days: int = 7) -> list:
        from django.db.models import Count, Avg
        since = target_date - timedelta(days=window_days)
        return list(
            EntityMention.objects.filter(mention_date__gte=since)
            .values('entity_name', 'entity_type')
            .annotate(
                mention_count=Count('id'),
                avg_sentiment=Avg('sentiment_score'),
            )
            .order_by('-mention_count')[:20]
        )

    def _build_articles_payload(self, enrichments) -> list:
        return [
            {
                'id':             e.article_id,
                'title':          e.article.title,
                'source':         e.article.source.name,
                'summary':        e.summary,
                'sentiment':      e.sentiment,
                'importance':     e.importance_score,
                'themes':         e.themes,
                'key_facts':      e.key_facts[:3],  # top 3 to save tokens
                'follow_up':      e.follow_up_worthy,
            }
            for e in enrichments
        ]

    def _call_llm(self, target_date: date, enrichments, trending) -> dict:
        articles_payload = self._build_articles_payload(enrichments)
        prompt = DAILY_DIGEST_USER.format(
            digest_date=str(target_date),
            article_count=len(enrichments),
            articles_json=json.dumps(articles_payload, indent=2),
            trending_entities_json=json.dumps(trending, indent=2),
        )
        llm_response = call_openai(
            system=DAILY_DIGEST_SYSTEM,
            user=prompt,
            model=DIGEST_MODEL,
        )
        parsed = parse_json_response(llm_response.content)
        parsed['_meta'] = {
            'input_tokens':  llm_response.input_tokens,
            'output_tokens': llm_response.output_tokens,
            'model':         llm_response.model,
        }
        return parsed

    def _save_digest(self, digest: DailyDigest, data: dict, enrichments):
        meta = data.pop('_meta', {})

        digest.digest_text       = data.get('digest_text', '')
        digest.top_stories       = data.get('top_stories', [])
        digest.trending_entities = data.get('trending_entities', [])
        digest.sector_sentiment  = data.get('sector_sentiment', {})
        digest.story_threads     = data.get('story_threads', [])
        digest.under_radar_story = data.get('under_radar_story', {})
        digest.key_concern       = data.get('key_concern', '')

        digest.articles_analyzed  = len(enrichments)
        digest.input_tokens_used  = meta.get('input_tokens', 0)
        digest.output_tokens_used = meta.get('output_tokens', 0)
        digest.model_used         = meta.get('model', '')
        digest.generated_at       = timezone.now()
        digest.is_published       = True

        digest.save()
