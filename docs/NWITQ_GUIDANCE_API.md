# NWITQ Feed Guidance APIs

These endpoints provide lightweight intelligence cards that can be inserted into feeds and article detail screens. They do not call an LLM at request time; they use existing clusters, alerts, enrichments, entity mentions, and source perspectives.

## Feed Interleaves

```http
POST /intelligence/feed/interleaves/
```

### Request

```json
{
  "surface": "home",
  "visible_article_ids": [123, 124, 125],
  "cursor": null,
  "limit": 6,
  "timezone": "Africa/Kampala"
}
```

### Response

```json
{
  "results": [
    {
      "id": "cluster-42",
      "insert_after_article_id": 125,
      "type": "cluster",
      "label": "NWITQ guide",
      "title": "Why this thread matters",
      "reason": "This story is appearing across 5 sources and 12 related articles.",
      "cta_label": "Open story thread",
      "target": {
        "route": "story_cluster",
        "slug": "governance"
      },
      "confidence": 0.86,
      "expires_at": "2026-06-12T12:00:00+03:00",
      "payload": {
        "surface": "home",
        "cluster_id": 42,
        "article_count": 12,
        "source_count": 5
      }
    }
  ],
  "next_cursor": "eyJvZmZzZXQiOiA2fQ=="
}
```

### Guide Types

- `cluster`: opens an existing story thread.
- `alert`: points to a high-signal story update.
- `entity`: points to entity coverage.
- `context`: points to article-level guidance.

### Cursor

The cursor is an opaque offset token. Pass `next_cursor` into the next request to get the next batch.

## Article Guidance

```http
GET /intelligence/articles/{id}/guidance/
```

### Response

```json
{
  "article": {
    "id": 123,
    "title": "Article title",
    "url": "https://example.com/article",
    "excerpt": "Short excerpt",
    "featured_image_url": "https://example.com/image.jpg",
    "source_name": "Daily Monitor",
    "category_name": "News",
    "author_name": "Reporter",
    "published_at": "2026-06-12T09:00:00+03:00",
    "read_time_minutes": 3
  },
  "related_cluster": {
    "id": 42,
    "title": "Governance story thread",
    "slug": "governance-story-thread",
    "summary": "Short cluster summary",
    "why_this_matters": "Why this story matters",
    "importance_score": 8,
    "article_count": 12,
    "source_count": 5
  },
  "key_entities": [
    {
      "entity_name": "Anita Among",
      "normalized_name": "anita among",
      "entity_type": "person"
    }
  ],
  "why_it_matters": "Why this article matters",
  "missing_context": [
    "Observed framing or missing context note"
  ],
  "suggested_next_reads": []
}
```

## Notes

- Both endpoints only return full-content articles.
- The feed endpoint may return an empty result list if there is not enough intelligence metadata around the visible articles.
- `confidence` is a ranking hint for UI display order, not a model probability.
