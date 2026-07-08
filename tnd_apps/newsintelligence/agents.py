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

from django.conf import settings
from django.utils import timezone

from .openai_client import (
    DIGEST_MODEL,
    ENRICHMENT_MODEL,
    call_openai,
    parse_json_response,
)
from .models import ArticleClaim, ArticleEnrichment, DailyDigest, EntityMention
from .entity_canonicalization import clean_entity_display_name, resolve_canonical_entity
from .schemas import validate_article_analysis, validate_daily_digest
from .prompts import (
    ARTICLE_ANALYSIS_SYSTEM,
    ARTICLE_ANALYSIS_USER,
    DAILY_DIGEST_SYSTEM,
    DAILY_DIGEST_USER,
    get_article_count_guidance,
)

logger = logging.getLogger(__name__)

MAX_CONTENT_WORDS = 450


def _truncate_content(text: str, max_words: int = MAX_CONTENT_WORDS) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    logger.warning("Content truncated from %d to %d words", len(words), max_words)
    return ' '.join(words[:max_words]) + '...'


# ── Agent 1: Article Analysis ─────────────────────────────────────────────────

class ArticleAnalysisAgent:
    """
    Processes a single Article with has_full_content=True.
    Creates or updates its ArticleEnrichment record.
    """

    def process(self, article) -> ArticleEnrichment:
        enrichment, _ = ArticleEnrichment.objects.get_or_create(article=article)

        if enrichment.status == 'completed':
            logger.debug("Article %d already enriched — skipping", article.id)
            return enrichment

        enrichment.status = 'processing'
        enrichment.save(update_fields=['status'])

        try:
            result = self._call_llm(article)
            self._save_enrichment(enrichment, result)
            logger.info("✓ Enriched article %d: %s", article.id, article.title[:60])
            try:
                from tnd_apps.cache_utils import on_enrichment_completed
                on_enrichment_completed(article.id)
            except Exception:
                pass
            return enrichment

        except ValueError as e:
            # Data issue (empty content, bad LLM JSON, missing keys) — not worth retrying
            enrichment.status = 'skipped'
            enrichment.error_message = str(e)
            enrichment.save(update_fields=['status', 'error_message'])
            logger.warning("⚠ Skipped article %d (%s): %s", article.id, article.title[:50], e)
            raise

        except Exception as e:
            # LLM / network error — mark failed and allow retry
            enrichment.status = 'failed'
            enrichment.error_message = str(e)
            enrichment.retry_count += 1
            enrichment.save(update_fields=['status', 'error_message', 'retry_count'])
            logger.error(
                "✗ Failed article %d (%s): %s",
                article.id, article.title[:50], e,
                exc_info=True
            )
            raise

    def _call_llm(self, article) -> dict:
        content = _truncate_content(article.content or article.excerpt or '')

        if not content.strip():
            raise ValueError(
                f"Article {article.id} has no content to analyze "
                f"(content={len(article.content or '')}, excerpt={len(article.excerpt or '')})"
            )

        source_name = article.source.name if article.source else 'Unknown'
        prompt = ARTICLE_ANALYSIS_USER.format(
            source=source_name,
            title=article.title,
            content=content,
        )
        llm_response = call_openai(
            system=ARTICLE_ANALYSIS_SYSTEM,
            user=prompt,
            model=ENRICHMENT_MODEL,
            max_tokens=1200,
        )
        parsed = parse_json_response(llm_response.content)
        parsed = validate_article_analysis(parsed, article)
        parsed['_meta'] = {
            'input_tokens':  llm_response.input_tokens,
            'output_tokens': llm_response.output_tokens,
            'model':         llm_response.model,
        }
        return parsed

    _REQUIRED_KEYS = {'summary', 'sentiment', 'importance_score', 'themes', 'key_facts', 'entities'}
    _VALID_SENTIMENTS = {'positive', 'negative', 'neutral', 'mixed'}

    def _save_enrichment(self, enrichment: ArticleEnrichment, data: dict):
        missing = self._REQUIRED_KEYS - data.keys()
        if missing:
            raise ValueError(f"LLM response missing required keys: {missing}")

        if data.get('sentiment') not in self._VALID_SENTIMENTS:
            logger.warning(
                "Unexpected sentiment %r for article %d — defaulting to 'neutral'",
                data.get('sentiment'), enrichment.article_id,
            )
            data['sentiment'] = 'neutral'

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
        enrichment.key_highlights   = data.get('key_highlights', [])
        enrichment.claims           = data.get('claims', [])
        enrichment.citations        = data.get('citations', [])
        enrichment.local_impact     = data.get('local_impact', {})
        enrichment.bias_or_framing_notes = data.get('bias_or_framing_notes', [])
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

        ArticleClaim.objects.filter(enrichment=enrichment).delete()
        ArticleClaim.objects.bulk_create([
            ArticleClaim(
                article=enrichment.article,
                enrichment=enrichment,
                claim_text=claim.get('claim', ''),
                evidence_text='',
                confidence=claim.get('confidence', 0.0),
            )
            for claim in enrichment.claims
            if claim.get('claim')
        ])


# ── Agent 2: Entity Extraction ────────────────────────────────────────────────

class EntityExtractionAgent:
    """
    Flattens entities from a completed ArticleEnrichment into
    individual EntityMention rows for trend detection queries.
    """

    def process(self, enrichment: ArticleEnrichment):
        if enrichment.status != 'completed':
            return

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
                    clean_name = clean_entity_display_name(name)
                    canonical = resolve_canonical_entity(clean_name, entity_type)
                    if not canonical:
                        continue
                    mentions.append(EntityMention(
                        enrichment=enrichment,
                        entity_name=clean_name,
                        normalized_name=canonical.normalized_name,
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
    """

    def generate(self, target_date: Optional[date] = None, force_refresh: bool = False) -> DailyDigest:
        if target_date is None:
            target_date = timezone.localdate()

        enrichments = self._fetch_enrichments(target_date)
        if not enrichments:
            raise ValueError(f"No completed enriched articles found for digest date {target_date}")

        digest, created = DailyDigest.objects.get_or_create(digest_date=target_date)

        if not created and digest.is_published and not force_refresh:
            logger.info("Digest for %s already published - skipping", target_date)
            return digest

        trending = self._get_trending_entities(target_date)

        try:
            result = self._call_llm(target_date, enrichments, trending)
            self._save_digest(digest, result, enrichments)
            logger.info(
                "Daily digest generated for %s (%d articles)",
                target_date, len(enrichments)
            )
        except Exception as e:
            logger.error("Failed to generate digest for %s: %s", target_date, e, exc_info=True)
            raise

        return digest

    def _fetch_enrichments(self, target_date: date):
        """
        Fetch the best enriched articles to include in the digest.

        The previous approach used calendar-date selectors with an early-return
        loop which caused two bugs:
          1. If even 1 article existed with published_at == target_date (UTC),
             the loop returned only those articles and ignored everything else.
          2. The server runs UTC but Uganda is UTC+3, so articles published
             during the Ugandan news day (06:00–22:00 EAT) span two UTC dates.
             A calendar-date filter always missed the morning articles.

        Fix: use a rolling 27-hour window anchored to NOW, not to midnight.
        At 05:30 UTC this covers 05:30 UTC yesterday → 05:30 UTC today, which
        is 08:30 EAT yesterday → 08:30 EAT today — the full Ugandan news day.
        Extend to 48 h if fewer than 10 articles are found so thin days still
        get a digest.
        """
        now = timezone.now()
        base_qs = (
            ArticleEnrichment.objects
            .filter(status='completed')
            .select_related('article', 'article__source')
        )

        # ── Primary: rolling 27-hour window from now ──────────────────────────
        # Covers the full previous Ugandan news day regardless of UTC date.
        window_start = now - timedelta(hours=27)
        enrichments = list(
            base_qs.filter(
                analyzed_at__gte=window_start,
            ).order_by('-importance_score', '-analyzed_at')[:50]
        )
        logger.info(
            "Digest fetch [27h window %s → %s]: %d enrichments",
            window_start.strftime('%Y-%m-%d %H:%M UTC'),
            now.strftime('%Y-%m-%d %H:%M UTC'),
            len(enrichments),
        )

        # ── Extend to 48 h if the 27-hour window is thin ─────────────────────
        # Happens on low-news days (public holidays, weekends).
        if len(enrichments) < 10:
            extended_start = now - timedelta(hours=48)
            seen_ids = {e.id for e in enrichments}
            extra = list(
                base_qs.filter(
                    analyzed_at__gte=extended_start,
                ).exclude(id__in=seen_ids)
                .order_by('-importance_score', '-analyzed_at')[:50 - len(enrichments)]
            )
            enrichments = enrichments + extra
            logger.info(
                "Digest fetch extended to 48h: %d total enrichments", len(enrichments)
            )

        # ── Hard fallback: most recently enriched regardless of date ──────────
        # Ensures a digest is always generated even on the first run when the
        # DB has older articles but no recent ones.
        if not enrichments:
            enrichments = list(
                base_qs.order_by('-importance_score', '-analyzed_at')[:50]
            )
            logger.warning("Digest fetch fell through to global fallback: %d enrichments", len(enrichments))

        return enrichments

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
                'story_arcs':     e.related_themes,
                'key_facts':      e.key_facts[:3],
                'local_impact':   e.local_impact,
                'follow_up':      e.follow_up_worthy,
                'controversy':    e.controversy_flag,
            }
            for e in enrichments
        ]

    def _call_llm(self, target_date: date, enrichments, trending) -> dict:
        articles_payload = self._build_articles_payload(enrichments)
        count = len(enrichments)
        prompt = DAILY_DIGEST_USER.format(
            digest_date=str(target_date),
            article_count=count,
            article_count_guidance=get_article_count_guidance(count),
            articles_json=json.dumps(articles_payload, indent=2),
            trending_entities_json=json.dumps(trending, indent=2),
        )
        llm_response = call_openai(
            system=DAILY_DIGEST_SYSTEM,
            user=prompt,
            model=DIGEST_MODEL,
            max_tokens=3000,
        )
        parsed = parse_json_response(llm_response.content)
        parsed = validate_daily_digest(parsed, [e.article_id for e in enrichments])
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
        digest.citations         = data.get('citations', [])
        digest.under_radar_story = data.get('under_radar_story', {})
        digest.key_concern       = data.get('key_concern', '')

        digest.articles_analyzed  = len(enrichments)
        digest.input_tokens_used  = meta.get('input_tokens', 0)
        digest.output_tokens_used = meta.get('output_tokens', 0)
        digest.model_used         = meta.get('model', '')
        digest.generated_at       = timezone.now()
        if getattr(settings, 'DIGEST_AUTO_PUBLISH', True):
            digest.editorial_review_status = 'approved'
            digest.is_published = True
        else:
            digest.editorial_review_status = data.get('editorial_review_status', 'needs_review')
            digest.is_published = digest.editorial_review_status == 'approved'

        digest.save()
        try:
            from tnd_apps.cache_utils import on_digest_published
            on_digest_published()
        except Exception:
            pass
