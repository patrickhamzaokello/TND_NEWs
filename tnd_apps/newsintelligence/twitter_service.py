"""
Twitter / X posting service for the daily digest.

Posts a threaded series of tweets after the morning digest is published.

Thread structure:
  Tweet 1 — opener: date, key concern excerpt, illustration image (if available)
  Tweet 2–N — one tweet per top story (title + why_it_matters + score)
  Last tweet — "under the radar" story + link to full digest

Requires tweepy >= 4.14:
  pip install tweepy

.env keys required:
  TWITTER_API_KEY
  TWITTER_API_SECRET
  TWITTER_ACCESS_TOKEN
  TWITTER_ACCESS_TOKEN_SECRET
"""

import logging
import os
import textwrap
from io import BytesIO

import tweepy
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

TWEET_MAX = 280          # hard Twitter limit
SITE_URL = getattr(settings, 'DIGEST_SITE_URL', 'https://newsapi.mwonya.com')
HASHTAGS = '#Uganda #UgandaNews #NWITQ'


# ── Client ────────────────────────────────────────────────────────────────────

def _get_client():
    """
    Returns a tweepy.Client (v2 API) with OAuth 1.0a credentials.
    Also returns the v1.1 API object for media uploads.
    """
    required = [
        settings.TWITTER_API_KEY,
        settings.TWITTER_API_SECRET,
        settings.TWITTER_ACCESS_TOKEN,
        settings.TWITTER_ACCESS_TOKEN_SECRET,
    ]
    if not all(required):
        raise RuntimeError(
            'Twitter credentials not fully configured. '
            'Set TWITTER_API_KEY, TWITTER_API_SECRET, '
            'TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET in .env'
        )

    client = tweepy.Client(
        consumer_key=settings.TWITTER_API_KEY,
        consumer_secret=settings.TWITTER_API_SECRET,
        access_token=settings.TWITTER_ACCESS_TOKEN,
        access_token_secret=settings.TWITTER_ACCESS_TOKEN_SECRET,
        wait_on_rate_limit=True,
    )
    return client


def _get_v1_api():
    """Returns the v1.1 API for media upload (still required by Twitter for images)."""
    auth = tweepy.OAuth1UserHandler(
        settings.TWITTER_API_KEY,
        settings.TWITTER_API_SECRET,
        settings.TWITTER_ACCESS_TOKEN,
        settings.TWITTER_ACCESS_TOKEN_SECRET,
    )
    return tweepy.API(auth)


# ── Text helpers ──────────────────────────────────────────────────────────────

def _trim(text: str, max_len: int, suffix: str = '…') -> str:
    """Trim text to max_len, appending suffix if truncated."""
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)].rstrip() + suffix


def _story_tweet(index: int, story: dict) -> str:
    """
    Format a single top story as a tweet.
    Example:
      2/ Parliament suspends NSSF reform debate
      MPs walked out after the Speaker ruled key clauses out of
      order — leaving 4M contributors without clarity on withdrawals.
      Importance: 8/10
    """
    prefix = f'{index}/'
    title = story.get('title', '')
    why = story.get('why_it_matters', '')
    score = story.get('importance_score', '')

    footer = f'\nImportance: {score}/10' if score else ''
    # Budget: max - prefix - newlines - footer
    body_budget = TWEET_MAX - len(prefix) - 2 - len(footer)

    # Fit title + why_it_matters into the budget
    if len(title) + 2 + len(why) <= body_budget:
        body = f'{title}\n{why}'
    elif len(title) + 2 <= body_budget:
        remaining = body_budget - len(title) - 2
        body = f'{title}\n{_trim(why, remaining)}'
    else:
        body = _trim(title, body_budget)

    return f'{prefix} {body}{footer}'


# ── Media upload ──────────────────────────────────────────────────────────────

def _upload_illustration(digest) -> str | None:
    """
    Upload the digest illustration to Twitter and return the media_id string.
    Returns None if no illustration exists or upload fails.
    Requires Twitter v1.1 API (media upload not yet in v2).
    """
    if not digest.illustration:
        return None

    try:
        img_path = digest.illustration.path  # local filesystem path
        api = _get_v1_api()
        media = api.media_upload(filename=img_path)
        logger.info('Twitter media upload OK | media_id=%s', media.media_id_string)
        return media.media_id_string
    except Exception as exc:
        logger.warning('Twitter media upload failed — posting without image: %s', exc)
        return None


# ── Thread builder ────────────────────────────────────────────────────────────

def _build_thread(digest) -> list[str]:
    """
    Build the ordered list of tweet texts for the digest thread.
    """
    date_str = digest.digest_date.strftime('%A, %-d %B %Y')
    tweets = []

    # ── Tweet 1: opener ───────────────────────────────────────────────────────
    key_concern = digest.key_concern or ''
    concern_budget = TWEET_MAX - len(date_str) - len(HASHTAGS) - 30
    concern_excerpt = _trim(key_concern, concern_budget)

    opener = (
        f'🗞 Uganda Daily Brief — {date_str}\n\n'
        f'{concern_excerpt}\n\n'
        f'{HASHTAGS}'
    )
    tweets.append(opener)

    # ── Tweets 2–N: top stories ───────────────────────────────────────────────
    top_stories = digest.top_stories or []
    for i, story in enumerate(top_stories[:4], start=2):
        tweets.append(_story_tweet(i, story))

    # ── Final tweet: under the radar + link ──────────────────────────────────
    under = digest.under_radar_story or {}
    link = f'{SITE_URL}/digest/{digest.digest_date}'

    if under.get('title'):
        u_title = under['title']
        u_reason = under.get('reason', '')
        footer = f'\n\n📖 Full brief → {link}'
        body_budget = TWEET_MAX - len(footer) - len('👁 Under the radar\n\n') - 10
        if len(u_title) + 2 + len(u_reason) <= body_budget:
            body = f'{u_title}\n{u_reason}'
        else:
            body = _trim(f'{u_title}\n{u_reason}', body_budget)
        tweets.append(f'👁 Under the radar\n\n{body}{footer}')
    else:
        tweets.append(f'📖 Read the full brief → {link}')

    return tweets


# ── Public API ────────────────────────────────────────────────────────────────

def post_digest_thread(digest) -> dict:
    """
    Post the daily digest as a Twitter thread.

    Returns:
      {status, thread_id, tweet_ids, tweet_count} on success
      {status: 'skipped', reason} if already posted or not published
      raises on unrecoverable error
    """
    if not digest.is_published:
        logger.warning('post_digest_thread: digest %s not published — skipping', digest.digest_date)
        return {'status': 'skipped', 'reason': 'not_published'}

    if digest.twitter_thread_id:
        logger.info(
            'post_digest_thread: digest %s already posted (thread_id=%s) — skipping',
            digest.digest_date, digest.twitter_thread_id,
        )
        return {'status': 'skipped', 'reason': 'already_posted', 'thread_id': digest.twitter_thread_id}

    client = _get_client()
    tweets = _build_thread(digest)
    media_id = _upload_illustration(digest)

    tweet_ids = []
    reply_to = None

    for i, text in enumerate(tweets):
        kwargs = {'text': text}

        # Attach illustration to the first tweet only
        if i == 0 and media_id:
            kwargs['media_ids'] = [media_id]

        # Chain replies for threads
        if reply_to:
            kwargs['in_reply_to_tweet_id'] = reply_to

        response = client.create_tweet(**kwargs)
        tweet_id = str(response.data['id'])
        tweet_ids.append(tweet_id)
        reply_to = tweet_id

        logger.info('Posted tweet %d/%d | id=%s', i + 1, len(tweets), tweet_id)

    thread_id = tweet_ids[0]

    # Persist so we don't post twice
    digest.twitter_thread_id = thread_id
    digest.twitter_posted_at = timezone.now()
    digest.save(update_fields=['twitter_thread_id', 'twitter_posted_at'])

    logger.info(
        'Digest thread posted | date=%s thread_id=%s tweets=%d',
        digest.digest_date, thread_id, len(tweet_ids),
    )
    return {
        'status': 'ok',
        'thread_id': thread_id,
        'tweet_ids': tweet_ids,
        'tweet_count': len(tweet_ids),
    }


def thread_url(thread_id: str) -> str:
    return f'https://x.com/i/web/status/{thread_id}'
