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

EDITORIAL_PROMPT = """Transform this image into a timeless editorial engraving illustration while preserving the original composition, pose, facial features, and perspective.
Apply a consistent monochrome visual treatment using:

* black and white only (no grayscale tint, no color)
* extremely high contrast
* dense stippling and halftone dot shading
* fine engraved crosshatching
* vintage woodcut / steel engraving textures
* newspaper print aesthetic
* etched line detail across clothing and skin
* crisp silhouette edges
* subtle distressed paper grain
* slightly worn print imperfections
* soft vignette around the edges
* cinematic lighting with crushed blacks and bright highlights

The result should feel like a premium editorial illustration printed in an old newspaper or magazine rather than a photograph.
Keep the subject instantly recognizable.
Avoid painterly effects, watercolor, digital painting, cartoon styles, anime, oil painting, CGI, photorealistic rendering, or excessive abstraction.
Style keywords: engraving, stippling, crosshatching, halftone, newsprint, woodcut, editorial illustration, vintage printmaking, monochrome, high contrast."""

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
        size='1024x1024',
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


# ── Public API ────────────────────────────────────────────────────────────────

def generate_editorial_image(enrichment) -> bool:
    """
    Generate an editorial-style image for the given ArticleEnrichment.

    1. Downloads article.featured_image_url
    2. Converts to PNG, resizes
    3. Calls OpenAI gpt-image-1 edit API
    4. Saves result to editorial_image field (media/editorial_images/)
    5. Sets editorial_image_generated_at

    Returns True on success, False on failure (errors are logged, not raised).
    """
    article = enrichment.article
    source_url = article.featured_image_url

    if not source_url:
        logger.warning(
            'generate_editorial_image: article %d has no featured_image_url — skipping',
            article.id,
        )
        return False

    logger.info(
        'Generating editorial image | article=%d (%s)',
        article.id, article.title[:60],
    )

    try:
        raw = _download_image(source_url)
        png_bytes = _to_png_bytes(raw)
        result_bytes = _call_openai_image_edit(png_bytes)

        # Save to the editorial_image ImageField.
        # Django handles placing it under MEDIA_ROOT/editorial_images/
        filename = f'{article.id}_{uuid.uuid4().hex[:8]}.png'
        enrichment.editorial_image.save(filename, ContentFile(result_bytes), save=False)
        enrichment.editorial_image_generated_at = timezone.now()
        enrichment.save(update_fields=['editorial_image', 'editorial_image_generated_at'])

        logger.info(
            'Editorial image saved | article=%d → %s',
            article.id, enrichment.editorial_image.name,
        )
        return True

    except requests.HTTPError as e:
        logger.error('Failed to download source image for article %d: %s', article.id, e)
    except ValueError as e:
        logger.error('Image processing error for article %d: %s', article.id, e)
    except Exception as e:
        logger.exception('Unexpected error generating editorial image for article %d: %s', article.id, e)

    return False
