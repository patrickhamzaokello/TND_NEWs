# Entity Top Articles API

Use this endpoint to fetch ranked full-content articles for a single entity.

## Endpoint

```http
GET /intelligence/entities/top-articles/?entity=uganda parliament&type=organization&page=1&page_size=10
```

## Query Parameters

- `entity` is required.
- `type` is optional. Supported values are `person`, `organization`, and `location`.
- `window_days` is optional. Defaults to `14`, capped at `90`.
- `page` is optional. Defaults to `1`.
- `page_size` is optional. Defaults to `10`, capped at `50`.
- `limit` is still accepted as a backward-compatible alias for `page_size` when `page_size` is not provided.

## Response

```json
{
  "entity": "uganda parliament",
  "type": "organization",
  "window_days": 14,
  "count": 42,
  "next": "https://api.example.com/intelligence/entities/top-articles/?entity=uganda%20parliament&page=2&page_size=10",
  "previous": null,
  "results": [
    {
      "id": 123,
      "title": "Parliament passes new bill",
      "slug": "parliament-passes-new-bill",
      "excerpt": "The article excerpt...",
      "source": "Daily Monitor",
      "featured_image_url": "https://example.com/image.jpg"
    }
  ]
}
```

Articles are ranked by enrichment importance, view count, mention recency, and scrape recency. Duplicate article mentions are collapsed so each article appears once.
