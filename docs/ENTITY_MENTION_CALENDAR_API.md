# Entity Mention Calendar API

Use this endpoint to render a GitHub-style activity graph for one entity across one calendar month.

## Endpoint

```http
GET /intelligence/entities/mention-calendar/?entity=uganda parliament&type=organization&month=2026-05
```

## Query Parameters

- `entity` is required. It can be the display name or normalized name of an entity.
- `type` is optional. Supported values are `person`, `organization`, and `location`.
- `month` is optional. Use `YYYY-MM`. If omitted, the API returns the current month.

## Matching

The API matches against `EntityMention.normalized_name` and `EntityMention.entity_name`.
If a canonical `Entity` exists, its `normalized_name` and aliases are also used.

Only mentions attached to completed enrichments and full-content articles from active sources are counted.

## Response

```json
{
  "entity": "uganda parliament",
  "normalized_names": ["parliament of uganda", "uganda parliament"],
  "type": "organization",
  "month": "2026-05",
  "start_date": "2026-05-01",
  "end_date": "2026-05-31",
  "max_count": 8,
  "total_mentions": 22,
  "total_articles": 18,
  "days": [
    {
      "date": "2026-05-01",
      "day": 1,
      "weekday": 4,
      "mention_count": 0,
      "article_count": 0,
      "level": 0
    },
    {
      "date": "2026-05-02",
      "day": 2,
      "weekday": 5,
      "mention_count": 3,
      "article_count": 2,
      "level": 2
    }
  ]
}
```

## Rendering Notes

- `days` always contains every day in the selected month, including days with zero mentions.
- `weekday` uses Python's convention: Monday is `0`, Sunday is `6`.
- `level` is a display intensity from `0` to `4`.
- `max_count` is the highest `mention_count` in the month and is used to calculate `level`.
- `mention_count` counts entity mention rows.
- `article_count` counts distinct full-content articles mentioning the entity on that date.

## Cost and Performance

This endpoint does not call an LLM. It uses existing `EntityMention` rows and one grouped database query per request.

The supporting database indexes are:

- `normalized_name, mention_date`
- `normalized_name, entity_type, mention_date`
