"""
Daily Digest email delivery via the Plunk REST API.

Plunk docs: https://useplunk.com/docs/api-reference/send-email
Endpoint: POST https://next-api.useplunk.com/v1/send
Auth:      Authorization: Bearer <EMAIL_PLUNK_API_KEY>

Sending schedule (EAT = UTC+3):
  08:35  morning  — full daily digest to all subscribers (daily + all_day)
  12:35  midday   — flash update with top new articles (all_day only)
  18:35  evening  — flash update with top new articles (all_day only)
  21:35  night    — flash update with top new articles (all_day only)
"""

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from .models import DigestSubscriber, DailyDigest

logger = logging.getLogger(__name__)

PLUNK_API_URL = 'https://next-api.useplunk.com/v1/send'
PLUNK_TIMEOUT = 15  # seconds per request

DIGEST_FROM = getattr(settings, 'DIGEST_FROM_EMAIL', 'hello@mwonya.com')
SITE_URL = getattr(settings, 'DIGEST_SITE_URL', 'https://newsapi.mwonya.com')
UNSUBSCRIBE_BASE = getattr(settings, 'DIGEST_UNSUBSCRIBE_URL', f'{SITE_URL}/digest/unsubscribe')


def _api_key() -> str:
    key = getattr(settings, 'EMAIL_PLUNK_API_KEY', '')
    if not key:
        raise RuntimeError('EMAIL_PLUNK_API_KEY is not set in settings / .env')
    return key


def _unsubscribe_url(token: str) -> str:
    return f'{UNSUBSCRIBE_BASE}?token={token}'


def _plunk_send(to: str, subject: str, html_body: str) -> bool:
    """POST a single email to the Plunk API. Returns True on success."""
    # Plunk requires a plain email address in `from` — strip any "Name <addr>" wrapper.
    import re
    from_email = DIGEST_FROM
    match = re.search(r'<([^>]+)>', from_email)
    if match:
        from_email = match.group(1)

    try:
        resp = requests.post(
            PLUNK_API_URL,
            headers={'Authorization': f'Bearer {_api_key()}'},
            json={
                'to': to,
                'subject': subject,
                'body': html_body,
                'from': from_email,
            },
            timeout=PLUNK_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            return True
        logger.error(
            'Plunk API error sending to %s: status=%d body=%s',
            to, resp.status_code, resp.text[:300],
        )
        return False
    except requests.exceptions.Timeout:
        logger.error('Plunk API timeout sending to %s', to)
        return False
    except Exception as exc:
        logger.error('Plunk API exception sending to %s: %s', to, exc)
        return False


def _enrich_stories(stories: list) -> list:
    """
    Attach image, source, author, and article URL to each story dict.
    All data fetched in a single query — no N+1.
    """
    from tnd_apps.news_scrapping.models import Article

    ids = [s['article_id'] for s in stories if s.get('article_id')]
    if not ids:
        return stories

    articles = (
        Article.objects.filter(id__in=ids)
        .select_related('source', 'author')
        .values(
            'id',
            'url',
            'featured_image_url',
            'source__name',
            'source__base_url',
            'author__name',
            'author__profile_url',
        )
    )
    article_map = {a['id']: a for a in articles}

    for story in stories:
        a = article_map.get(story.get('article_id')) or {}
        story['image_url']     = a.get('featured_image_url') or ''
        story['article_url']   = a.get('url') or ''
        story['source_name']   = a.get('source__name') or ''
        story['source_url']    = a.get('source__base_url') or ''
        story['author_name']   = a.get('author__name') or ''
        story['author_url']    = a.get('author__profile_url') or ''
    return stories


def _build_context(digest: DailyDigest, subscriber_name: str, unsubscribe_url: str) -> dict:
    top_stories = _enrich_stories(list(digest.top_stories or []))

    under_radar = digest.under_radar_story or {}
    if under_radar.get('title'):
        under_radar = _enrich_stories([dict(under_radar)])[0]
    else:
        under_radar = None

    return {
        'digest_date': str(digest.digest_date),
        'digest_date_display': digest.digest_date.strftime('%A, %d %B %Y'),
        'subscriber_name': subscriber_name,
        'digest_text': digest.digest_text,
        'key_concern': digest.key_concern,
        'top_stories': top_stories,
        'trending_entities': (digest.trending_entities or [])[:10],
        'under_radar': under_radar,
        'articles_analyzed': digest.articles_analyzed,
        'unsubscribe_url': unsubscribe_url,
        'site_url': SITE_URL,
    }


# ── Slot metadata ─────────────────────────────────────────────────────────────

SLOT_CONFIG = {
    # slot        label      window_hours  min_importance  subject_prefix
    'morning': ('Morning',   None,         6,              'NWITQ Morning Brief'),
    'evening': ('Evening',   10,           6,              'NWITQ Evening Roundup'),
}


# ── Morning: full digest ───────────────────────────────────────────────────────

def _send_one(digest: DailyDigest, subscriber: DigestSubscriber) -> bool:
    """Render and send the full morning digest to a single subscriber."""
    ctx = _build_context(
        digest,
        subscriber_name=subscriber.name or '',
        unsubscribe_url=_unsubscribe_url(subscriber.unsubscribe_token),
    )
    html_body = render_to_string('newsintelligence/email/daily_digest.html', ctx)
    date_display = digest.digest_date.strftime('%A, %d %B %Y')
    subject = f'NWITQ Morning Brief — {date_display}'

    ok = _plunk_send(subscriber.email, subject, html_body)
    if ok:
        subscriber.mark_sent(digest.digest_date, slot='morning')
        logger.info('Morning digest sent → %s', subscriber.email)
    return ok


def send_digest_to_all(digest: DailyDigest) -> dict:
    """
    Send the full morning digest to all active confirmed subscribers who
    haven't already received today's edition.
    """
    if not digest.is_published:
        logger.warning('Digest %s is not published — skipping', digest.digest_date)
        return {'sent': 0, 'failed': 0, 'total': 0}

    # All active confirmed subscribers — morning goes to everyone (daily + all_day)
    subscribers = DigestSubscriber.objects.filter(
        is_active=True,
        confirmed=True,
    ).exclude(last_digest_date=digest.digest_date)

    total = subscribers.count()
    sent = failed = 0
    logger.info('Morning digest %s → %d subscribers', digest.digest_date, total)

    for sub in subscribers.iterator():
        if _send_one(digest, sub):
            sent += 1
        else:
            failed += 1

    logger.info('Morning digest done | sent=%d failed=%d', sent, failed)
    return {'sent': sent, 'failed': failed, 'total': total}


def send_digest_to_email(digest: DailyDigest, email: str) -> bool:
    """One-off test send — does not touch subscriber records."""
    ctx = _build_context(
        digest,
        subscriber_name='',
        unsubscribe_url=f'{UNSUBSCRIBE_BASE}?token=test-token',
    )
    html_body = render_to_string('newsintelligence/email/daily_digest.html', ctx)
    date_display = digest.digest_date.strftime('%A, %d %B %Y')
    subject = f'[TEST] NWITQ Morning Brief — {date_display}'

    ok = _plunk_send(email, subject, html_body)
    if ok:
        logger.info('Test digest email sent to %s', email)
    else:
        logger.error('Test digest email failed for %s', email)
    return ok


# ── Flash updates: midday / evening / night ───────────────────────────────────

def _get_flash_articles(window_hours: int, min_importance: int = 7, limit: int = 5) -> list:
    """
    Fetch articles enriched within the last `window_hours` with importance
    at or above `min_importance`, ordered best-first.
    Returns plain dicts ready for the template.
    """
    from .models import ArticleEnrichment

    cutoff = timezone.now() - timedelta(hours=window_hours)
    enrichments = (
        ArticleEnrichment.objects
        .filter(status='completed', importance_score__gte=min_importance, analyzed_at__gte=cutoff)
        .select_related('article', 'article__source', 'article__author')
        .order_by('-importance_score', '-analyzed_at')[:limit]
    )

    articles = []
    for e in enrichments:
        a = e.article
        articles.append({
            'title':          a.title,
            'url':            a.url or '',
            'summary':        e.summary,
            'importance_score': e.importance_score,
            'image_url':      a.featured_image_url or '',
            'source_name':    a.source.name if a.source else '',
            'source_url':     a.source.base_url if a.source else '',
            'author_name':    a.author.name if a.author else '',
            'author_url':     a.author.profile_url if a.author else '',
        })
    return articles


def _send_flash_one(slot: str, articles: list, date_display: str, subscriber: DigestSubscriber) -> bool:
    """Render and send a flash update to a single subscriber."""
    label, _, _, subject_prefix = SLOT_CONFIG[slot]
    ctx = {
        'slot_label':       label,
        'digest_date':      str(timezone.localdate()),
        'digest_date_display': date_display,
        'subscriber_name':  subscriber.name or '',
        'articles':         articles,
        'article_count':    len(articles),
        'unsubscribe_url':  _unsubscribe_url(subscriber.unsubscribe_token),
        'site_url':         SITE_URL,
    }
    html_body = render_to_string('newsintelligence/email/flash_update.html', ctx)
    subject = f'{subject_prefix} — {date_display}'

    ok = _plunk_send(subscriber.email, subject, html_body)
    if ok:
        subscriber.mark_sent(timezone.localdate(), slot=slot)
        logger.info('Flash [%s] sent → %s', slot, subscriber.email)
    return ok


def send_flash_update(slot: str) -> dict:
    """
    Send a flash update for the given slot to all `all_day` subscribers.
    Skips subscribers who already received this slot today.

    Returns: {sent, failed, total, articles_found}
    """
    if slot not in SLOT_CONFIG:
        logger.error('Unknown slot %r — must be one of %s', slot, list(SLOT_CONFIG))
        return {'sent': 0, 'failed': 0, 'total': 0, 'articles_found': 0}

    _, window_hours, min_importance, _ = SLOT_CONFIG[slot]
    today = timezone.localdate()
    date_display = today.strftime('%A, %d %B %Y')

    articles = _get_flash_articles(window_hours, min_importance)
    if not articles:
        logger.info('Flash [%s]: no articles with importance>=%d in last %dh — skipping',
                    slot, min_importance, window_hours)
        return {'sent': 0, 'failed': 0, 'total': 0, 'articles_found': 0}

    subscribers = DigestSubscriber.objects.filter(
        is_active=True,
        confirmed=True,
        frequency='morning_evening',
    ).exclude(last_digest_date=today, last_slot_sent=slot)

    total = subscribers.count()
    sent = failed = 0
    logger.info('Flash [%s] → %d subscribers | %d articles', slot, total, len(articles))

    for sub in subscribers.iterator():
        if _send_flash_one(slot, articles, date_display, sub):
            sent += 1
        else:
            failed += 1

    logger.info('Flash [%s] done | sent=%d failed=%d', slot, sent, failed)
    return {'sent': sent, 'failed': failed, 'total': total, 'articles_found': len(articles)}


def send_flash_to_email(slot: str, email: str) -> bool:
    """One-off test flash send — does not touch subscriber records."""
    if slot not in SLOT_CONFIG:
        logger.error('Unknown slot %r', slot)
        return False

    _, window_hours, min_importance, subject_prefix = SLOT_CONFIG[slot]
    label = SLOT_CONFIG[slot][0]
    today = timezone.localdate()
    date_display = today.strftime('%A, %d %B %Y')

    articles = _get_flash_articles(window_hours, min_importance)
    if not articles:
        logger.warning('Flash [%s] test: no articles found — sending empty preview', slot)

    ctx = {
        'slot_label':          label,
        'digest_date':         str(today),
        'digest_date_display': date_display,
        'subscriber_name':     '',
        'articles':            articles,
        'article_count':       len(articles),
        'unsubscribe_url':     f'{UNSUBSCRIBE_BASE}?token=test-token',
        'site_url':            SITE_URL,
    }
    html_body = render_to_string('newsintelligence/email/flash_update.html', ctx)
    subject = f'[TEST] {subject_prefix} — {date_display}'

    ok = _plunk_send(email, subject, html_body)
    if ok:
        logger.info('Test flash [%s] sent to %s', slot, email)
    else:
        logger.error('Test flash [%s] failed for %s', slot, email)
    return ok
