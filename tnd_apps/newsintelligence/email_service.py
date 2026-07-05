"""
Daily Digest email delivery service.

Sends the published DailyDigest to all active, confirmed DigestSubscribers.
Uses Django's built-in email backend (configured in settings via EMAIL_HOST etc.)
and renders the HTML template at:
    newsintelligence/email/daily_digest.html
"""

import logging
from datetime import date

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from .models import DigestSubscriber, DailyDigest

logger = logging.getLogger(__name__)

# Sender shown in email clients — override in settings if needed
DEFAULT_FROM = getattr(settings, 'DIGEST_FROM_EMAIL', 'TNDNEWS Daily <digest@mwonya.com>')
SITE_URL = getattr(settings, 'DIGEST_SITE_URL', 'https://newsapi.mwonya.com')
UNSUBSCRIBE_BASE = getattr(settings, 'DIGEST_UNSUBSCRIBE_URL', f'{SITE_URL}/digest/unsubscribe')


def _unsubscribe_url(token: str) -> str:
    return f'{UNSUBSCRIBE_BASE}?token={token}'


def _build_context(digest: DailyDigest, subscriber: DigestSubscriber) -> dict:
    under_radar = digest.under_radar_story or {}
    return {
        'digest_date': str(digest.digest_date),
        'digest_date_display': digest.digest_date.strftime('%A, %d %B %Y'),
        'subscriber_name': subscriber.name or '',
        'digest_text': digest.digest_text,
        'key_concern': digest.key_concern,
        'top_stories': digest.top_stories or [],
        'trending_entities': (digest.trending_entities or [])[:10],
        'under_radar': under_radar if under_radar.get('title') else None,
        'articles_analyzed': digest.articles_analyzed,
        'unsubscribe_url': _unsubscribe_url(subscriber.unsubscribe_token),
        'site_url': SITE_URL,
    }


def _send_one(digest: DailyDigest, subscriber: DigestSubscriber) -> bool:
    """Render and send the digest email to a single subscriber. Returns True on success."""
    ctx = _build_context(digest, subscriber)
    html_body = render_to_string('newsintelligence/email/daily_digest.html', ctx)
    text_body = strip_tags(html_body)

    subject = f'TNDNEWS Morning Brief — {ctx["digest_date_display"]}'
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=DEFAULT_FROM,
        to=[subscriber.email],
    )
    msg.attach_alternative(html_body, 'text/html')

    try:
        msg.send(fail_silently=False)
        subscriber.mark_sent(digest.digest_date)
        logger.info('Digest email sent → %s', subscriber.email)
        return True
    except Exception as exc:
        logger.error('Failed to send digest to %s: %s', subscriber.email, exc)
        return False


def send_digest_to_all(digest: DailyDigest) -> dict:
    """
    Send `digest` to every active, confirmed subscriber who hasn't already
    received this date's edition.

    Returns a summary dict: {sent, skipped, failed, total}.
    """
    if not digest.is_published:
        logger.warning('Digest %s is not published — skipping email send', digest.digest_date)
        return {'sent': 0, 'skipped': 0, 'failed': 0, 'total': 0}

    subscribers = DigestSubscriber.objects.filter(
        is_active=True,
        confirmed=True,
    ).exclude(
        last_digest_date=digest.digest_date,   # already received today's edition
    )

    total = subscribers.count()
    sent = skipped = failed = 0

    logger.info('Sending digest %s to %d subscribers', digest.digest_date, total)

    for sub in subscribers.iterator():
        if _send_one(digest, sub):
            sent += 1
        else:
            failed += 1

    logger.info(
        'Digest email run complete | date=%s sent=%d failed=%d skipped=%d',
        digest.digest_date, sent, failed, skipped,
    )
    return {'sent': sent, 'skipped': skipped, 'failed': failed, 'total': total}


def send_digest_to_email(digest: DailyDigest, email: str) -> bool:
    """
    Send a one-off digest email to a specific address (for testing).
    Does not update any subscriber stats.
    """
    ctx = {
        'digest_date': str(digest.digest_date),
        'digest_date_display': digest.digest_date.strftime('%A, %d %B %Y'),
        'subscriber_name': 'Test Reader',
        'digest_text': digest.digest_text,
        'key_concern': digest.key_concern,
        'top_stories': digest.top_stories or [],
        'trending_entities': (digest.trending_entities or [])[:10],
        'under_radar': digest.under_radar_story if (digest.under_radar_story or {}).get('title') else None,
        'articles_analyzed': digest.articles_analyzed,
        'unsubscribe_url': f'{UNSUBSCRIBE_BASE}?token=test-token',
        'site_url': SITE_URL,
    }
    html_body = render_to_string('newsintelligence/email/daily_digest.html', ctx)
    text_body = strip_tags(html_body)

    subject = f'[TEST] TNDNEWS Morning Brief — {ctx["digest_date_display"]}'
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=DEFAULT_FROM,
        to=[email],
    )
    msg.attach_alternative(html_body, 'text/html')
    try:
        msg.send(fail_silently=False)
        logger.info('Test digest email sent to %s', email)
        return True
    except Exception as exc:
        logger.error('Test digest email failed for %s: %s', email, exc)
        return False
