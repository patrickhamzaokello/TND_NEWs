"""
Daily Digest email delivery via the Plunk REST API.

Plunk docs: https://useplunk.com/docs/api-reference/send-email
Endpoint: POST https://next-api.useplunk.com/v1/send
Auth:      Authorization: Bearer <EMAIL_PLUNK_API_KEY>
"""

import logging

import requests
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from .models import DigestSubscriber, DailyDigest

logger = logging.getLogger(__name__)

PLUNK_API_URL = 'https://next-api.useplunk.com/v1/send'
PLUNK_TIMEOUT = 15  # seconds per request

DIGEST_FROM = getattr(settings, 'DIGEST_FROM_EMAIL', 'digest@mwonya.com')
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
    try:
        resp = requests.post(
            PLUNK_API_URL,
            headers={'Authorization': f'Bearer {_api_key()}'},
            json={
                'to': to,
                'subject': subject,
                'body': html_body,
                'from': DIGEST_FROM,
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


def _build_context(digest: DailyDigest, subscriber_name: str, unsubscribe_url: str) -> dict:
    under_radar = digest.under_radar_story or {}
    return {
        'digest_date': str(digest.digest_date),
        'digest_date_display': digest.digest_date.strftime('%A, %d %B %Y'),
        'subscriber_name': subscriber_name,
        'digest_text': digest.digest_text,
        'key_concern': digest.key_concern,
        'top_stories': digest.top_stories or [],
        'trending_entities': (digest.trending_entities or [])[:10],
        'under_radar': under_radar if under_radar.get('title') else None,
        'articles_analyzed': digest.articles_analyzed,
        'unsubscribe_url': unsubscribe_url,
        'site_url': SITE_URL,
    }


def _send_one(digest: DailyDigest, subscriber: DigestSubscriber) -> bool:
    """Render and send the digest to a single subscriber. Returns True on success."""
    ctx = _build_context(
        digest,
        subscriber_name=subscriber.name or '',
        unsubscribe_url=_unsubscribe_url(subscriber.unsubscribe_token),
    )
    html_body = render_to_string('newsintelligence/email/daily_digest.html', ctx)
    subject = f'TNDNEWS Morning Brief — {ctx["digest_date_display"]}'

    ok = _plunk_send(subscriber.email, subject, html_body)
    if ok:
        subscriber.mark_sent(digest.digest_date)
        logger.info('Digest email sent → %s', subscriber.email)
    return ok


def send_digest_to_all(digest: DailyDigest) -> dict:
    """
    Send `digest` to every active, confirmed subscriber who hasn't already
    received this date's edition.

    Returns: {sent, failed, total}
    """
    if not digest.is_published:
        logger.warning('Digest %s is not published — skipping email send', digest.digest_date)
        return {'sent': 0, 'failed': 0, 'total': 0}

    subscribers = DigestSubscriber.objects.filter(
        is_active=True,
        confirmed=True,
    ).exclude(last_digest_date=digest.digest_date)

    total = subscribers.count()
    sent = failed = 0

    logger.info('Sending digest %s to %d subscribers via Plunk', digest.digest_date, total)

    for sub in subscribers.iterator():
        if _send_one(digest, sub):
            sent += 1
        else:
            failed += 1

    logger.info(
        'Digest email run complete | date=%s sent=%d failed=%d',
        digest.digest_date, sent, failed,
    )
    return {'sent': sent, 'failed': failed, 'total': total}


def send_digest_to_email(digest: DailyDigest, email: str) -> bool:
    """
    Send a one-off digest to a specific address (testing).
    Does not update any subscriber records.
    """
    ctx = _build_context(
        digest,
        subscriber_name='',
        unsubscribe_url=f'{UNSUBSCRIBE_BASE}?token=test-token',
    )
    html_body = render_to_string('newsintelligence/email/daily_digest.html', ctx)
    subject = f'[TEST] TNDNEWS Morning Brief — {ctx["digest_date_display"]}'

    ok = _plunk_send(email, subject, html_body)
    if ok:
        logger.info('Test digest email sent to %s', email)
    else:
        logger.error('Test digest email failed for %s', email)
    return ok
