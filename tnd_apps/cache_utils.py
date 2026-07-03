"""
Shared Redis caching utilities for TNDNEWS.

Usage
-----
from tnd_apps.cache_utils import cached_response, invalidate, TTL

# In a view action:
def top_story(self, request):
    key = CacheKey.TOP_STORY
    return cached_response(key, TTL.TOP_STORY, lambda: self._build_top_story())

# Invalidating after a write:
invalidate(CacheKey.TOP_STORY, CacheKey.FEATURED)

Design
------
- All keys are automatically prefixed with the CACHES KEY_PREFIX ('tndnews')
  by django-redis, so we don't double-prefix here.
- TTLs are deliberately short for feed data (5–15 min) because the scraper
  runs every 3 hours and enrichment every hour.  Long TTLs (1 h+) are only
  used for data that almost never changes (categories, sources).
- Per-user feeds are NOT cached here because they depend on the user's
  followed sources.  Only the shared/public surfaces are cached.
- Pattern-based invalidation uses django-redis delete_pattern() which runs a
  Redis SCAN — safe on large keyspaces but slightly slower than a single DEL.
  Use it only for wildcard invalidations (e.g. all cluster detail pages).
"""

import hashlib
import logging
from functools import wraps

from django.core.cache import cache

logger = logging.getLogger(__name__)


# ── TTL constants (seconds) ────────────────────────────────────────────────────

class TTL:
    TOP_STORY          = 5 * 60        # 5 min  — changes when enrichment runs
    FEATURED           = 5 * 60        # 5 min
    TRENDING           = 5 * 60        # 5 min  — view velocity changes fast
    TOP_READS          = 10 * 60       # 10 min — engagement scores move slowly
    LATEST             = 3 * 60        # 3 min  — pure chronological, most volatile
    ARTICLE_DETAIL     = 10 * 60       # 10 min — invalidated on enrichment update
    ARTICLE_GUIDANCE   = 10 * 60       # 10 min — cluster / entity data
    ARTICLE_SUMMARY    = 0             # never — summary lives in the DB; skip Redis
    SEARCH_SUGGESTIONS = 3 * 60        # 3 min  — tied to recent article titles
    CATEGORIES         = 60 * 60       # 1 hour — almost never changes
    SOURCES            = 30 * 60       # 30 min — source list is stable
    DIGEST_TODAY       = 20 * 60       # 20 min — regenerated 4×/day
    DIGEST_DATE        = 60 * 60       # 1 hour — past dates are final
    CLUSTER_LIST       = 10 * 60       # 10 min — rebuilt hourly at :45
    CLUSTER_DETAIL     = 15 * 60       # 15 min
    TRENDING_ENTITIES  = 10 * 60       # 10 min — entity mention counts
    ENTITY_CALENDAR    = 30 * 60       # 30 min — historical, barely changes
    TOP_ENTITIES       = 10 * 60       # 10 min


# ── Cache key builders ─────────────────────────────────────────────────────────

class CacheKey:
    # Static keys (no params)
    TOP_STORY          = 'v1:article:top_story'
    FEATURED           = 'v1:article:featured'
    CATEGORIES         = 'v1:categories'
    SOURCES            = 'v1:sources'
    DIGEST_TODAY       = 'v1:digest:today'
    CLUSTER_LIST       = 'v1:clusters:list'

    # Parametric key builders
    @staticmethod
    def article_detail(article_id: int) -> str:
        return f'v1:article:{article_id}'

    @staticmethod
    def article_guidance(article_id: int) -> str:
        return f'v1:article:{article_id}:guidance'

    @staticmethod
    def top_reads(days: int) -> str:
        return f'v1:article:top_reads:days={days}'

    @staticmethod
    def trending(hours: int = 24) -> str:
        return f'v1:article:trending:h={hours}'

    @staticmethod
    def latest(hours) -> str:
        return f'v1:article:latest:h={hours or "all"}'

    @staticmethod
    def search_suggestions(query: str, limit: int) -> str:
        q_hash = hashlib.md5(query.lower().encode()).hexdigest()[:8]
        return f'v1:search:suggest:{q_hash}:{limit}'

    @staticmethod
    def digest_date(date_str: str) -> str:
        return f'v1:digest:{date_str}'

    @staticmethod
    def cluster_detail(slug: str) -> str:
        return f'v1:cluster:{slug}'

    @staticmethod
    def trending_entities(window_days: int) -> str:
        return f'v1:entities:trending:w={window_days}'

    @staticmethod
    def entity_calendar(entity: str, entity_type: str, month: str) -> str:
        slug = hashlib.md5(f'{entity}:{entity_type}:{month}'.encode()).hexdigest()[:10]
        return f'v1:entity:calendar:{slug}'

    @staticmethod
    def top_entities(entity_limit: int, articles_per: int, window_days: int) -> str:
        return f'v1:entities:top:{entity_limit}:{articles_per}:{window_days}'

    @staticmethod
    def cluster_list_page(page: int, page_size: int, status: str, theme: str) -> str:
        return f'v1:clusters:list:p={page}:ps={page_size}:s={status or ""}:t={theme or ""}'


# ── Core helpers ───────────────────────────────────────────────────────────────

def cached_response(key: str, timeout: int, compute_fn):
    """
    Return cached value for `key`, or call `compute_fn()`, cache its result,
    and return it.  Returns None (no caching) when timeout is 0.

    `compute_fn` must return a JSON-serializable value (dict, list, etc.).
    Failures in the cache layer are silently ignored so they never break views.
    """
    if timeout == 0:
        return compute_fn()

    try:
        cached = cache.get(key)
        if cached is not None:
            logger.debug('Cache HIT  %s', key)
            return cached
    except Exception:
        logger.warning('Cache GET failed for %s', key, exc_info=True)

    value = compute_fn()

    try:
        cache.set(key, value, timeout=timeout)
        logger.debug('Cache SET  %s  ttl=%ds', key, timeout)
    except Exception:
        logger.warning('Cache SET failed for %s', key, exc_info=True)

    return value


def invalidate(*keys: str) -> None:
    """Delete one or more specific cache keys."""
    for key in keys:
        try:
            cache.delete(key)
            logger.debug('Cache DEL  %s', key)
        except Exception:
            logger.warning('Cache DEL failed for %s', key, exc_info=True)


def invalidate_pattern(pattern: str) -> None:
    """
    Delete all keys matching a glob pattern.
    Requires django-redis (uses SCAN under the hood — safe on large keyspaces).
    """
    try:
        # django-redis prepends KEY_PREFIX automatically, so we include it here
        # to match the actual stored keys.
        from django.conf import settings
        prefix = settings.CACHES.get('default', {}).get('KEY_PREFIX', '')
        full_pattern = f'{prefix}:{pattern}' if prefix else pattern
        deleted = cache.delete_pattern(full_pattern)
        logger.debug('Cache DEL pattern %s  (%s keys)', full_pattern, deleted)
    except Exception:
        logger.warning('Cache DEL pattern failed for %s', pattern, exc_info=True)


# ── Invalidation helpers (called from signals and tasks) ──────────────────────

def on_article_published(article_id: int) -> None:
    """
    Called when a new article with has_full_content=True is saved.
    Clears surfaces that show the latest articles.
    """
    invalidate(
        CacheKey.TOP_STORY,
        CacheKey.FEATURED,
        CacheKey.article_detail(article_id),
    )
    invalidate_pattern('v1:article:latest:*')


def on_enrichment_completed(article_id: int) -> None:
    """
    Called when ArticleEnrichment for an article reaches status='completed'.
    Enrichment changes the intelligence-ordered surfaces and the article detail.
    """
    invalidate(
        CacheKey.TOP_STORY,
        CacheKey.FEATURED,
        CacheKey.article_detail(article_id),
        CacheKey.article_guidance(article_id),
    )
    invalidate_pattern('v1:article:top_reads:*')
    invalidate_pattern('v1:article:trending:*')


def on_digest_published() -> None:
    """Called when today's digest is generated or republished."""
    invalidate(CacheKey.DIGEST_TODAY)


def on_clusters_rebuilt() -> None:
    """Called after build_story_clusters runs."""
    invalidate(CacheKey.CLUSTER_LIST)
    invalidate_pattern('v1:cluster:*')
    invalidate_pattern('v1:clusters:list:*')
    invalidate_pattern('v1:entities:trending:*')
    invalidate_pattern('v1:entities:top:*')
