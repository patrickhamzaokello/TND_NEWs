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


def _trim_to_sentence(text: str, max_len: int) -> str:
    """
    Trim to max_len cutting at the last sentence boundary (. ! ?) before the
    limit. Falls back to a word boundary with ellipsis if no sentence end found.
    """
    if len(text) <= max_len:
        return text
    chunk = text[:max_len]
    for i in range(len(chunk) - 1, max(0, len(chunk) - 120), -1):
        if chunk[i] in '.!?' and (i + 1 >= len(chunk) or chunk[i + 1] in ' \n'):
            return chunk[:i + 1].rstrip()
    last_space = chunk.rfind(' ')
    if last_space > 0:
        return chunk[:last_space].rstrip() + '…'
    return chunk.rstrip() + '…'


def _first_sentences(text: str, max_len: int, n: int = 2) -> str:
    """
    Return the first n complete sentences from text.
    If even the first sentence exceeds max_len, trims at word boundary.
    """
    sentences = []
    remaining = text.strip()
    while remaining and len(sentences) < n:
        # Find next sentence boundary
        end = -1
        for i, ch in enumerate(remaining):
            if ch in '.!?' and (i + 1 >= len(remaining) or remaining[i + 1] in ' \n'):
                end = i
                break
        if end == -1:
            # No sentence end found — treat rest as one sentence
            sentences.append(remaining.strip())
            break
        sentences.append(remaining[:end + 1].strip())
        remaining = remaining[end + 1:].strip()

    result = ' '.join(sentences)
    if len(result) <= max_len:
        return result
    # Trim at word boundary as last resort
    chunk = result[:max_len]
    last_space = chunk.rfind(' ')
    return (chunk[:last_space].rstrip() if last_space > 0 else chunk.rstrip()) + '…'


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

def _split_into_tweets(text: str, max_len: int) -> list[str]:
    """
    Split a long text into tweet-sized chunks, breaking at sentence boundaries.
    Each chunk is at most max_len characters.
    """
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    chunks = []

    for para in paragraphs:
        if len(para) <= max_len:
            chunks.append(para)
            continue

        # Split paragraph at sentence boundaries
        current = ''
        # Simple sentence splitter — split after . ! ? followed by space
        import re
        sentences = re.split(r'(?<=[.!?])\s+', para)
        for sentence in sentences:
            if not current:
                current = sentence
            elif len(current) + 1 + len(sentence) <= max_len:
                current = current + ' ' + sentence
            else:
                if current:
                    chunks.append(current)
                current = sentence
        if current:
            chunks.append(current)

    return chunks


def _build_thread(digest) -> list[str]:
    """
    Build the daily digest as a Twitter thread from the digest_text narrative.

    Structure:
      Tweet 1  — date header + key_concern_short hook + hashtags
      Tweet 2+ — digest_text split into tweet-sized paragraphs
      Last     — link to full brief
    """
    date_str = digest.digest_date.strftime('%A, %-d %B %Y')
    link = f'{SITE_URL}/digest/{digest.digest_date}'
    tweets = []

    # ── Tweet 1: opener ───────────────────────────────────────────────────────
    hook = (digest.key_concern_short or digest.key_concern or '').strip()
    hook_budget = TWEET_MAX - len(date_str) - len(HASHTAGS) - 30
    if len(hook) > hook_budget:
        hook = _first_sentences(hook, hook_budget, n=1)

    opener = f'🗞 Uganda Daily Brief — {date_str}\n\n{hook}\n\n{HASHTAGS}'
    tweets.append(opener)

    # ── Tweets 2+: digest narrative ───────────────────────────────────────────
    digest_text = (digest.digest_text or '').strip()
    if digest_text:
        body_chunks = _split_into_tweets(digest_text, TWEET_MAX)
        tweets.extend(body_chunks)

    # ── Final tweet: link ─────────────────────────────────────────────────────
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
