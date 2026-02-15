# news_enrichment — Django App

AI-powered enrichment pipeline for scraped news articles.
Sits on top of your existing `news` app and adds a Silver + Gold data layer.

---

## Architecture

```
news.Article (Bronze)
      │  has_full_content=True
      ▼
ArticleEnrichment (Silver)   ← ArticleAnalysisAgent + EntityExtractionAgent
      │
      ├── EntityMention rows  ← trend detection
      │
      ▼
DailyDigest (Gold)           ← DailyDigestAgent
```

---

## Installation

### 1. Install dependencies

```bash
pip install anthropic
```

### 2. Add to INSTALLED_APPS

```python
# settings.py
INSTALLED_APPS = [
    ...
    'news',                # your existing app
    'news_enrichment',     # ← add this
]
```

### 3. Add settings

```python
# settings.py

ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY')  # required

# Optional: override default models
ENRICHMENT_MODEL = 'claude-haiku-4-5-20251001'   # bulk article analysis
DIGEST_MODEL     = 'claude-sonnet-4-5-20250929'  # daily digest synthesis
```

### 4. Update the app name reference

In `models.py` and `services.py`, replace `'news'` with your actual
Django app name that contains the `Article` model:

```python
# models.py  (line ~30)
article = models.OneToOneField('YOUR_APP.Article', ...)

# services.py  (line ~120)
Article = apps.get_model('YOUR_APP', 'Article')
```

In `migrations/0001_initial.py`, update the dependency:
```python
dependencies = [
    ('YOUR_APP', '0001_initial'),
]
```

### 5. Run migrations

```bash
python manage.py migrate news_enrichment
```

### 6. Add Celery beat schedule (optional but recommended)

```python
# settings.py
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'enrich-articles-hourly': {
        'task': 'news_enrichment.tasks.enrich_new_articles',
        'schedule': crontab(minute=15),
    },
    'retry-failed-enrichments': {
        'task': 'news_enrichment.tasks.retry_failed_enrichments',
        'schedule': crontab(minute=0, hour='*/6'),
    },
    'generate-daily-digest': {
        'task': 'news_enrichment.tasks.generate_daily_digest',
        'schedule': crontab(minute=0, hour=6),
    },
}
```

---

## Usage

### Management commands (manual / testing)

```bash
# Enrich all pending articles
python manage.py enrich_articles

# Preview what would be processed (no API calls)
python manage.py enrich_articles --dry-run

# Enrich with custom batch size
python manage.py enrich_articles --batch-size 100

# Retry previously failed articles
python manage.py enrich_articles --retry-failed

# Generate yesterday's daily digest
python manage.py enrich_articles --digest

# Generate digest for a specific date (backfill)
python manage.py enrich_articles --digest-date 2026-02-14

# View pipeline stats and costs
python manage.py enrich_articles --stats
```

### Python API

```python
from news_enrichment.services import EnrichmentService
from news_enrichment.agents import ArticleAnalysisAgent

# Run full pipeline
service = EnrichmentService(batch_size=30)
run = service.run_enrichment()
print(f"Processed: {run.articles_processed}, Cost: ${run.estimated_cost_usd}")

# Process a single article
from news.models import Article
article = Article.objects.get(id=123)
agent = ArticleAnalysisAgent()
enrichment = agent.process(article)
print(enrichment.summary)
print(enrichment.themes)
print(enrichment.entities_people)

# Get today's digest
digest = service.run_daily_digest()
```

### Querying enriched data

```python
from news_enrichment.models import ArticleEnrichment, EntityMention, DailyDigest

# High-importance articles from today
ArticleEnrichment.objects.filter(
    importance_score__gte=7,
    analyzed_at__date=date.today(),
).select_related('article').order_by('-importance_score')

# Articles flagged for follow-up
ArticleEnrichment.objects.filter(follow_up_worthy=True).select_related('article')

# Top entities this week
from django.db.models import Count
EntityMention.objects.filter(
    mention_date__gte=date.today() - timedelta(days=7)
).values('entity_name', 'entity_type').annotate(
    count=Count('id')
).order_by('-count')[:10]

# Latest digest
digest = DailyDigest.objects.filter(is_published=True).latest('digest_date')
print(digest.digest_text)
print(digest.top_stories)
```

---

## File Structure

```
news_enrichment/
├── __init__.py
├── apps.py               # AppConfig
├── models.py             # ArticleEnrichment, EntityMention, DailyDigest, EnrichmentRun
├── agents.py             # ArticleAnalysisAgent, EntityExtractionAgent, DailyDigestAgent
├── services.py           # EnrichmentService (orchestrator)
├── claude_client.py      # Anthropic API wrapper + cost tracking
├── prompts.py            # All LLM prompts (versioned here)
├── tasks.py              # Celery tasks
├── admin.py              # Django admin
├── migrations/
│   ├── __init__.py
│   └── 0001_initial.py
└── management/
    └── commands/
        └── enrich_articles.py
```

---

## Cost Reference (per run)

| Articles/day | Daily cost | Monthly |
|---|---|---|
| 25  | ~$0.06  | ~$1.80  |
| 100 | ~$0.24  | ~$7.20  |
| 300 | ~$0.72  | ~$21.60 |

*Digest synthesis (Sonnet) adds ~$0.04-0.09/day regardless of volume.*

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✓ | — | Your Anthropic API key |
| `ENRICHMENT_MODEL` | ✗ | `claude-haiku-4-5-20251001` | Model for article analysis |
| `DIGEST_MODEL` | ✗ | `claude-sonnet-4-5-20250929` | Model for digest synthesis |
