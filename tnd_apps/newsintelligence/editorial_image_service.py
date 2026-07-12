"""
Editorial image generation service.

Takes an article's featured_image_url, sends it to OpenAI gpt-image-1 (image
edit API) with a fixed engraving/halftone prompt, saves the result to
media/editorial_images/<article_id>.png, and updates
ArticleEnrichment.editorial_image.

Requires:
  - OPENAI_API_KEY in settings
  - Pillow  (pip install Pillow)
  - requests (already in requirements)
"""

import io
import logging
import os
import uuid
from pathlib import Path

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

EDITORIAL_PROMPT = """Transform this image into a premium modern editorial illustration in the style of high-end magazine art direction — the kind used by The Atlantic, The New Yorker, and Bloomberg Businessweek.

Visual treatment:
* Render the main subject using extremely dense stippling and dot-work as the primary texture — no smooth gradients, only accumulated dots and marks
* Apply fine engraved crosshatching and etched line detail on clothing, faces, skin, and surfaces
* Use high contrast with deep crushed blacks and bright highlights
* Preserve the original composition, pose, and subject recognisably

Color direction:
* Use 1–2 bold flat accent colors (examples: magenta, vermillion orange, electric teal, acid yellow, cobalt blue) against a predominantly black-and-white stippled base
* Apply flat color as background blocks, sky areas, or as a single glowing element (sun, moon, burst) — not across the whole image
* The engraved/stippled subject sits against or in front of these flat color areas
* Alternatively: render the entire scene in full color using only stippling and dot-work — no smooth fills anywhere, every area built from dense colored dots

Composition and finish:
* Crisp silhouette edges on the subject
* Flat bold color shapes in background contrast with intricate stippled foreground detail
* No distressed paper grain or worn texture — keep it clean and contemporary
* Wide landscape crop (16:9 feel) with breathing room around the subject

Avoid: photorealism, painterly brushwork, watercolor, oil painting, smooth gradients, CGI rendering, anime, cartoon outlines, excessive abstraction, grayscale washes.

Style keywords: stippling, dot-work, editorial illustration, engraving, modern magazine art, flat color accent, halftone, crosshatching, high contrast, The Atlantic illustration style."""

EDITORIAL_IMAGE_MODEL = 'gpt-image-1'
DOWNLOAD_TIMEOUT = 20   # seconds to download the source image
OPENAI_TIMEOUT = 120    # seconds for image generation (can be slow)
MAX_SOURCE_BYTES = 20 * 1024 * 1024  # 20 MB — OpenAI limit


# ── Helpers ───────────────────────────────────────────────────────────────────

def _download_image(url: str) -> bytes:
    """Download an image URL and return raw bytes. Raises on failure."""
    resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    resp.raise_for_status()

    content_type = resp.headers.get('content-type', '')
    if not content_type.startswith('image/'):
        raise ValueError(f"URL did not return an image (content-type: {content_type})")

    data = b''
    for chunk in resp.iter_content(chunk_size=65536):
        data += chunk
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError(f"Source image exceeds {MAX_SOURCE_BYTES // (1024*1024)} MB limit")
    return data


def _to_png_bytes(raw: bytes) -> bytes:
    """
    Convert image bytes to PNG using Pillow.
    OpenAI's image edit API requires PNG or JPEG; PNG is safest.
    Also resizes to ≤ 1024px on the long side to keep the payload small.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert('RGB')

    # Resize so neither dimension exceeds 1024px (preserves aspect ratio)
    max_dim = 1024
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _call_openai_image_edit(png_bytes: bytes) -> bytes:
    """
    Send the PNG to OpenAI gpt-image-1 image edit endpoint.
    Returns the generated image as raw PNG bytes.
    """
    import base64
    import openai

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY, timeout=OPENAI_TIMEOUT)

    # gpt-image-1 returns b64_json by default; response_format param not supported
    response = client.images.edit(
        model=EDITORIAL_IMAGE_MODEL,
        image=('source.png', png_bytes, 'image/png'),
        prompt=EDITORIAL_PROMPT,
        n=1,
        size='1536x1024',
    )

    item = response.data[0]

    # Prefer b64_json if present, fall back to downloading the URL
    if getattr(item, 'b64_json', None):
        return base64.b64decode(item.b64_json)

    if getattr(item, 'url', None):
        resp = requests.get(item.url, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        return resp.content

    raise ValueError('OpenAI returned neither b64_json nor url in image edit response')


# ── Digest illustration ───────────────────────────────────────────────────────

ILLUSTRATION_PROMPT_TEMPLATE = """Transform this news photograph into a premium modern editorial illustration for today's top story: "{title}".

{base_prompt}

This illustration will appear at the top of a daily news digest. Make it striking, conceptual, and immediately communicative of the story's significance."""

ILLUSTRATION_TEXT_PROMPT_TEMPLATE = """Create a premium modern editorial illustration for a news digest. Today's top story: "{title}". Context: {context}

Visual treatment:
* Build the entire scene using extremely dense stippling and dot-work as the primary texture
* Apply fine engraved crosshatching on figures, objects, and surfaces
* Use high contrast with deep crushed blacks and bright highlights
* Choose 1–2 bold flat accent colors (examples: vermillion, electric teal, acid yellow, cobalt blue, magenta) against a black-and-white stippled base — apply as sky, background blocks, or a glowing element
* Alternatively render in full color using only stippling — every area built from dense colored dots, no smooth fills
* Crisp silhouette edges on subjects
* Wide landscape composition (16:9) with strong visual hierarchy
* Clean contemporary finish — no distressed paper or worn texture

Represent the story symbolically or metaphorically — do not depict specific named people.
Avoid: photorealism, painterly brushwork, watercolor, smooth gradients, CGI, anime, cartoon outlines.
Style keywords: stippling, dot-work, editorial illustration, engraving, modern magazine art, flat color accent, The Atlantic illustration style."""

CAPTION_SYSTEM = (
    'You are an editorial caption writer for a premium news digest. '
    'Write one short sentence (max 15 words) that works as an evocative illustration caption. '
    'Do not start with "An illustration of", "A depiction of", or "Illustration inspired by". '
    'Return only the caption text, no quotes, no punctuation at the end.'
)


def _generate_caption(title: str, key_concern: str) -> str:
    """Generate a short editorial caption using GPT."""
    from .openai_client import _get_client
    client = _get_client()
    user_text = f'Top story: {title}\nKey concern: {key_concern}'
    resp = client.chat.completions.create(
        model='gpt-4o-mini',
        max_completion_tokens=60,
        messages=[
            {'role': 'system', 'content': CAPTION_SYSTEM},
            {'role': 'user', 'content': user_text},
        ],
    )
    return (resp.choices[0].message.content or '').strip()


def _call_openai_image_generate(prompt: str) -> bytes:
    """Text-to-image via gpt-image-1 when no source photo is available."""
    import base64
    import openai

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY, timeout=OPENAI_TIMEOUT)
    response = client.images.generate(
        model=EDITORIAL_IMAGE_MODEL,
        prompt=prompt,
        n=1,
        size='1536x1024',
    )
    item = response.data[0]
    if getattr(item, 'b64_json', None):
        return base64.b64decode(item.b64_json)
    if getattr(item, 'url', None):
        resp = requests.get(item.url, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    raise ValueError('OpenAI returned neither b64_json nor url in image generate response')


def _record_illustration(digest, status: str, error: str = ''):
    digest.illustration_last_attempt = timezone.now()
    digest.illustration_status = status
    digest.illustration_error = error[:1000]
    digest.save(update_fields=['illustration_last_attempt', 'illustration_status', 'illustration_error'])


def _try_img2img(story: dict) -> bytes | None:
    """
    Try to generate an img2img illustration from a top story's featured image.
    Returns image bytes on success, None if no image or download fails.
    Raises openai.BadRequestError with code='moderation_blocked' if flagged.
    """
    from tnd_apps.news_scrapping.models import Article
    article_id = story.get('article_id')
    if not article_id:
        return None
    try:
        article = Article.objects.get(pk=article_id)
    except Article.DoesNotExist:
        return None
    if not article.featured_image_url:
        return None
    try:
        raw = _download_image(article.featured_image_url)
    except Exception as e:
        logger.warning('Could not download image for article %s: %s', article_id, e)
        return None
    png_bytes = _to_png_bytes(raw)
    prompt = ILLUSTRATION_PROMPT_TEMPLATE.format(title=story.get('title', ''), base_prompt=EDITORIAL_PROMPT)
    # Let moderation errors propagate so the caller can skip to the next story
    return _call_openai_image_edit_with_prompt(png_bytes, prompt)


def generate_digest_illustration(digest) -> bool:
    """
    Generate an editorial illustration for a DailyDigest.

    Strategy:
      1. Try img2img on each top story in order — skip stories whose images are
         moderation-blocked and move to the next one.
      2. If all top stories are blocked or have no image, fall back to text2img
         using the first top story's title + why_it_matters.

    Caption references the story the image was drawn from so readers understand
    the connection.

    Returns True on success, False on skip/failure.
    """
    import openai as _openai

    top_stories = digest.top_stories or []
    if not top_stories:
        logger.warning('generate_digest_illustration: digest %s has no top stories', digest.digest_date)
        _record_illustration(digest, 'skipped', 'No top stories in digest')
        return False

    key_concern = digest.key_concern or ''
    result_bytes = None
    source_story = None  # the story whose image was actually used

    # ── Try img2img across top stories ───────────────────────────────────────
    for story in top_stories:
        title = story.get('title', '')
        try:
            result_bytes = _try_img2img(story)
            if result_bytes is not None:
                source_story = story
                logger.info(
                    'Digest img2img succeeded | date=%s story="%s"',
                    digest.digest_date, title[:60],
                )
                break
        except _openai.BadRequestError as e:
            code = getattr(e, 'code', None) or (e.body or {}).get('code', '')
            if code == 'moderation_blocked':
                logger.warning(
                    'Digest img2img blocked for story "%s" — trying next story', title[:60]
                )
                continue
            raise

    # ── Fall back to text2img using first story ───────────────────────────────
    if result_bytes is None:
        top = top_stories[0]
        source_story = top
        context = top.get('why_it_matters') or key_concern or top.get('title', '')
        prompt = ILLUSTRATION_TEXT_PROMPT_TEMPLATE.format(
            title=top.get('title', ''), context=context
        )
        logger.info(
            'Digest illustration falling back to text2img | date=%s story="%s"',
            digest.digest_date, top.get('title', '')[:60],
        )
        try:
            result_bytes = _call_openai_image_generate(prompt)
        except _openai.BadRequestError as e:
            code = getattr(e, 'code', None) or (e.body or {}).get('code', '')
            if code == 'moderation_blocked':
                logger.warning('Digest text2img also blocked | date=%s', digest.digest_date)
                _record_illustration(digest, 'moderation', str(e)[:500])
                return False
            raise

    # ── Generate caption referencing the source story ─────────────────────────
    source_title = source_story.get('title', '') if source_story else ''
    caption = _generate_caption(source_title, key_concern)
    # Prefix caption with a story reference so readers know the connection
    if source_title:
        caption = f'Illustration inspired by: {source_title}. {caption}'

    # ── Save ──────────────────────────────────────────────────────────────────
    try:
        filename = f'{digest.digest_date}_{uuid.uuid4().hex[:8]}.png'
        digest.illustration.save(filename, ContentFile(result_bytes), save=False)
        digest.illustration_caption = caption
        digest.illustration_generated_at = timezone.now()
        digest.illustration_last_attempt = timezone.now()
        digest.illustration_status = 'generated'
        digest.illustration_error = ''
        digest.save(update_fields=[
            'illustration', 'illustration_caption', 'illustration_generated_at',
            'illustration_last_attempt', 'illustration_status', 'illustration_error',
        ])
        logger.info('Digest illustration saved | date=%s caption="%s"', digest.digest_date, caption[:80])
        return True

    except Exception as e:
        logger.exception('Failed to save digest illustration for %s: %s', digest.digest_date, e)
        _record_illustration(digest, 'error', str(e)[:500])
        raise


def _call_openai_image_edit_with_prompt(png_bytes: bytes, prompt: str) -> bytes:
    """img2img with a custom prompt (used for digest illustrations)."""
    import base64
    import openai

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY, timeout=OPENAI_TIMEOUT)
    response = client.images.edit(
        model=EDITORIAL_IMAGE_MODEL,
        image=('source.png', png_bytes, 'image/png'),
        prompt=prompt,
        n=1,
        size='1536x1024',
    )
    item = response.data[0]
    if getattr(item, 'b64_json', None):
        return base64.b64decode(item.b64_json)
    if getattr(item, 'url', None):
        resp = requests.get(item.url, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    raise ValueError('OpenAI returned neither b64_json nor url')


# ── Public API ────────────────────────────────────────────────────────────────

def _record_editorial(enrichment, status: str, error: str = ''):
    enrichment.editorial_image_last_attempt = timezone.now()
    enrichment.editorial_image_status = status
    enrichment.editorial_image_error = error[:1000]
    enrichment.save(update_fields=[
        'editorial_image_last_attempt', 'editorial_image_status', 'editorial_image_error'
    ])


def generate_editorial_image(enrichment) -> bool:
    """
    Generate an editorial-style image for the given ArticleEnrichment.

    Returns True on success, False on skip/moderation (errors are logged, not raised
    for permanent failures; re-raised for transient ones so Celery can retry).
    """
    article = enrichment.article
    source_url = article.featured_image_url

    if not source_url:
        logger.warning(
            'generate_editorial_image: article %d has no featured_image_url — skipping',
            article.id,
        )
        _record_editorial(enrichment, 'skipped', 'No featured_image_url on article')
        return False

    logger.info('Generating editorial image | article=%d (%s)', article.id, article.title[:60])

    try:
        raw = _download_image(source_url)
        png_bytes = _to_png_bytes(raw)
        result_bytes = _call_openai_image_edit(png_bytes)

        filename = f'{article.id}_{uuid.uuid4().hex[:8]}.png'
        enrichment.editorial_image.save(filename, ContentFile(result_bytes), save=False)
        enrichment.editorial_image_generated_at = timezone.now()
        enrichment.editorial_image_last_attempt = timezone.now()
        enrichment.editorial_image_status = 'generated'
        enrichment.editorial_image_error = ''
        enrichment.save(update_fields=[
            'editorial_image', 'editorial_image_generated_at',
            'editorial_image_last_attempt', 'editorial_image_status', 'editorial_image_error',
        ])

        logger.info('Editorial image saved | article=%d → %s', article.id, enrichment.editorial_image.name)
        return True

    except requests.HTTPError as e:
        logger.error('Failed to download source image for article %d: %s', article.id, e)
        _record_editorial(enrichment, 'download_error', str(e))
    except ValueError as e:
        logger.error('Image processing error for article %d: %s', article.id, e)
        _record_editorial(enrichment, 'api_error', str(e))
    except Exception as e:
        import openai as _openai
        if isinstance(e, _openai.BadRequestError):
            code = getattr(e, 'code', None) or (e.body or {}).get('code', '')
            if code == 'moderation_blocked':
                categories = (
                    (e.body or {})
                    .get('error', {})
                    .get('moderation_details', {})
                    .get('categories', [])
                )
                logger.warning(
                    'Editorial image blocked by moderation | article=%d categories=%s',
                    article.id, categories,
                )
                _record_editorial(enrichment, 'moderation', f'categories={categories}')
                return False
        logger.exception('Unexpected error generating editorial image for article %d: %s', article.id, e)
        _record_editorial(enrichment, 'error', str(e))
        raise

    return False
