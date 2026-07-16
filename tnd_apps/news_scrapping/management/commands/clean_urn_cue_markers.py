"""
One-off backfill: strip broadcast cue markers (//Cue in: ... Cue out ...//)
and residual &nbsp; entities from already-scraped Uganda Radio Network articles.

New scrapes are already clean via UrnScraper._strip_cue_markers — this only
fixes articles saved before that cleanup was added.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from tnd_apps.news_scrapping.models import Article
from tnd_apps.news_scrapping.urn_scrapper import UrnScraper


class Command(BaseCommand):
    help = "Strip radio cue markers from already-saved Uganda Radio Network article bodies."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        articles = Article.objects.filter(source__name="Uganda Radio Network")

        inspected = updated = 0
        batch = []

        for article in articles.iterator(chunk_size=200):
            inspected += 1
            original = article.content or ""
            if not original:
                continue

            cleaned = original.replace("\xa0", " ").replace("&nbsp;", " ")
            cleaned = UrnScraper._strip_cue_markers(cleaned)
            # Re-run the same paragraph collapsing used at scrape time
            import re
            paragraphs = [
                re.sub(r"\s+", " ", p).strip()
                for p in re.split(r"\n\s*\n", cleaned)
            ]
            cleaned = "\n\n".join(p for p in paragraphs if p and len(p) > 2)

            if cleaned == original:
                continue

            updated += 1
            if dry_run:
                self.stdout.write(f"Would clean article {article.id}: {article.title[:70]}")
                continue

            article.content = cleaned
            article.word_count = len(cleaned.split())
            article.paragraph_count = len(paragraphs)
            article.content_hash = Article._hash_text(cleaned or article.excerpt)
            article.updated_at = timezone.now()
            batch.append(article)

            if len(batch) >= 100:
                Article.objects.bulk_update(
                    batch, ["content", "word_count", "paragraph_count", "content_hash", "updated_at"]
                )
                batch = []

        if batch:
            Article.objects.bulk_update(
                batch, ["content", "word_count", "paragraph_count", "content_hash", "updated_at"]
            )

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"Inspected {inspected} URN articles. {action}: {updated}."))
