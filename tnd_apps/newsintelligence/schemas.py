"""
Validation helpers for AI-generated JSON.

These checks are intentionally small and dependency-light. They catch the
failure modes that matter most before model output is stored: missing fields,
wrong primitive types, invalid article IDs, unsupported enums, and malformed
citations.
"""

VALID_HIGHLIGHT_TYPES = {'fact', 'figure', 'claim', 'link'}


VALID_THEMES = {
    'governance', 'education', 'health', 'economy', 'entertainment', 'sports',
    'crime', 'environment', 'technology', 'politics', 'social', 'business',
    'infrastructure', 'agriculture', 'tourism',
}
VALID_SENTIMENTS = {'positive', 'negative', 'neutral', 'mixed'}
VALID_ENTITY_TYPES = {'person', 'organization', 'location'}


def _require(data, key, expected_type):
    if key not in data:
        raise ValueError(f"AI response missing required key: {key}")
    if not isinstance(data[key], expected_type):
        raise ValueError(f"AI response key {key!r} must be {expected_type.__name__}")


def _coerce_score(value, minimum, maximum, key):
    if not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    return max(minimum, min(maximum, value))


def validate_article_analysis(data, article):
    _require(data, 'summary', str)
    _require(data, 'sentiment', str)
    _require(data, 'importance_score', int)
    _require(data, 'themes', list)
    _require(data, 'key_facts', list)
    _require(data, 'entities', dict)

    if data['sentiment'] not in VALID_SENTIMENTS:
        data['sentiment'] = 'neutral'

    data['importance_score'] = int(_coerce_score(data['importance_score'], 1, 10, 'importance_score'))
    data['themes'] = [theme for theme in data['themes'][:4] if theme in VALID_THEMES]
    data['key_facts'] = [str(fact) for fact in data['key_facts'][:6] if str(fact).strip()]

    sentiment_score = data.get('sentiment_score')
    if sentiment_score is not None:
        data['sentiment_score'] = float(_coerce_score(sentiment_score, -1.0, 1.0, 'sentiment_score'))

    entities = data.get('entities') or {}
    data['entities'] = {
        'people': [str(item).strip() for item in entities.get('people', []) if str(item).strip()],
        'organizations': [str(item).strip() for item in entities.get('organizations', []) if str(item).strip()],
        'locations': [str(item).strip() for item in entities.get('locations', []) if str(item).strip()],
    }

    data['citations'] = _normalize_citations(data.get('citations'), [article.id], article)
    data['claims'] = _normalize_claims(data.get('claims'), article.id)
    data['local_impact'] = data.get('local_impact') if isinstance(data.get('local_impact'), dict) else {}
    data['bias_or_framing_notes'] = (
        data.get('bias_or_framing_notes')
        if isinstance(data.get('bias_or_framing_notes'), list)
        else []
    )
    data['key_highlights'] = _normalize_highlights(data.get('key_highlights'))
    return data


def validate_daily_digest(data, valid_article_ids):
    import logging
    _log = logging.getLogger(__name__)

    valid_ids = {int(article_id) for article_id in valid_article_ids}
    _require(data, 'digest_text', str)
    _require(data, 'top_stories', list)
    _require(data, 'sector_sentiment', dict)

    # Filter out hallucinated article IDs rather than hard-failing the digest.
    valid_stories = []
    for story in data.get('top_stories', []):
        article_id = story.get('article_id')
        if article_id not in valid_ids:
            _log.warning('Digest: dropping top_story with unknown article_id=%s', article_id)
        else:
            valid_stories.append(story)
    data['top_stories'] = valid_stories

    valid_threads = []
    for thread in data.get('story_threads', []) or []:
        bad = [aid for aid in thread.get('article_ids', []) if aid not in valid_ids]
        if bad:
            _log.warning('Digest: dropping story_thread with unknown article_ids=%s', bad)
        else:
            valid_threads.append(thread)
    data['story_threads'] = valid_threads

    under_radar = data.get('under_radar_story') or {}
    if under_radar and under_radar.get('article_id') not in valid_ids:
        _log.warning(
            'Digest: clearing under_radar_story with unknown article_id=%s',
            under_radar.get('article_id'),
        )
        data['under_radar_story'] = {}

    data['citations'] = _normalize_citations(data.get('citations'), valid_ids)
    return data


def _normalize_claims(claims, default_article_id):
    if not isinstance(claims, list):
        return []
    normalized = []
    for claim in claims[:12]:
        if not isinstance(claim, dict) or not claim.get('claim'):
            continue
        normalized.append({
            'claim': str(claim.get('claim', '')).strip(),
            'source_article_id': int(claim.get('source_article_id') or default_article_id),
            'evidence_text': str(claim.get('evidence_text', '')).strip()[:500],
            'confidence': float(_coerce_score(claim.get('confidence', 0.5), 0.0, 1.0, 'confidence')),
        })
    return normalized


def _normalize_highlights(highlights) -> list:
    """
    Validate and clean key_highlights from the LLM.
    Each item must have a non-empty 'text' and a valid 'type'.
    The 'url' key is optional and only kept for link-type highlights.
    """
    if not isinstance(highlights, list):
        return []
    result = []
    seen_texts = set()
    for item in highlights[:8]:  # cap at 8 highlights per article
        if not isinstance(item, dict):
            continue
        text = str(item.get('text', '')).strip()
        h_type = str(item.get('type', '')).strip().lower()
        if not text or h_type not in VALID_HIGHLIGHT_TYPES:
            continue
        if text in seen_texts:
            continue  # deduplicate
        seen_texts.add(text)
        entry = {'text': text, 'type': h_type}
        if h_type == 'link' and item.get('url'):
            url = str(item['url']).strip()
            if url.startswith('http'):
                entry['url'] = url
        result.append(entry)
    return result


def _normalize_citations(citations, valid_article_ids, article=None):
    valid_ids = {int(article_id) for article_id in valid_article_ids}
    normalized = []

    if article is not None:
        normalized.append({
            'article_id': article.id,
            'url': article.url,
            'title': article.title,
            'source': article.source.name if article.source else '',
        })

    if not isinstance(citations, list):
        return normalized

    for citation in citations[:20]:
        if not isinstance(citation, dict):
            continue
        article_id = citation.get('article_id')
        if article_id not in valid_ids:
            continue
        normalized.append({
            'article_id': int(article_id),
            'url': str(citation.get('url', '')).strip(),
            'title': str(citation.get('title', '')).strip(),
            'source': str(citation.get('source', '')).strip(),
            'evidence_text': str(citation.get('evidence_text', '')).strip()[:500],
        })
    return normalized
