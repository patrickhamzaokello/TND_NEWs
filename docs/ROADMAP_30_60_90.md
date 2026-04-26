# News Intelligence Platform Roadmap

## 30 Days: Safety, Stability, and Mobile Readiness

- Fix API permissions so normal users can read feeds and manage personal actions, while article/source/category writes remain staff-only.
- Repair Celery Beat task names and remove references to missing tasks.
- Rotate all credentials found in local environment files and move production secrets to a managed secret store.
- Restore a testable Python environment and run `manage.py check`, migrations, and a smoke-test scrape/enrichment path in CI.
- Split mobile article list/detail serializers so feeds do not ship full article bodies.
- Remove raw SQL search fallbacks and use ORM-safe ranking.
- Register or remove Django signals so breaking-news detection has deterministic behavior.
- Add a source scrape health dashboard endpoint with latest run status, 24-hour article counts, and error counts.

## 60 Days: Intelligence Foundations

- Backfill canonical URLs, normalized title hashes, and content hashes for duplicate detection.
- Add PostgreSQL full-text search indexes and tune search ranking by title, excerpt, content, source, and category.
- Introduce story clusters, cluster-article links, entity aliases, timeline events, source perspectives, article claims, and citations.
- Validate AI outputs with strict schemas before saving.
- Add digest lookup endpoints for today and specific dates.
- Add citations to article enrichments and daily digests.
- Add Sentry/OpenTelemetry hooks for request, task, scrape, and OpenAI observability.

## 90 Days: Product Differentiation

- Build multi-source perspective comparison for important story clusters.
- Generate timelines of evolving stories from clustered article events.
- Send alerts for important updates, not merely every newly scraped article.
- Maintain source reliability metadata and source-specific editorial notes.
- Add local Uganda impact analysis by region, sector, affected groups, and time horizon.
- Expose topic and entity trend APIs for the React Native app.
- Add editorial review tooling for AI-generated digests before publishing when `DIGEST_AUTO_PUBLISH=False`.
- Use `/intelligence/digests/<id>/approve/` or `/intelligence/digests/<id>/reject/` for staff review workflows.
- Use `python manage.py send_story_alerts --dry-run` before enabling scheduled story-alert delivery.

## Operational Notes

- Run `python manage.py backfill_article_identity` after applying the new article identity migration.
- Run `python manage.py build_story_clusters --days=7` to populate initial clusters from existing enrichments.
- Keep `.env` local-only. Use `.env.example` for onboarding and rotate any credential that was ever present in a local or shared file.
