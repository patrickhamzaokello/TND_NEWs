from django.core.management.base import BaseCommand
from django.utils import timezone

from tnd_apps.news_scrapping.models import Article
from tnd_apps.news_scrapping.text_cleaning import clean_article_text


class Command(BaseCommand):
    help = "Clean saved article text fields for display-safe apostrophes, quotes, and whitespace."

    def add_arguments(self, parser):
        parser.add_argument("--source-id", type=int, help="Only clean articles from one source.")
        parser.add_argument("--limit", type=int, help="Maximum number of articles to inspect.")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        queryset = Article.objects.all().order_by("id")
        if options["source_id"]:
            queryset = queryset.filter(source_id=options["source_id"])
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        inspected = 0
        updated = 0
        batch = []
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        for article in queryset.iterator(chunk_size=batch_size):
            inspected += 1
            changed = False

            cleaned_title = clean_article_text(article.title, preserve_paragraphs=False)
            cleaned_excerpt = clean_article_text(article.excerpt, preserve_paragraphs=False)
            cleaned_content = clean_article_text(article.content)
            cleaned_caption = clean_article_text(article.image_caption, preserve_paragraphs=False)

            if article.title != cleaned_title:
                article.title = cleaned_title
                changed = True
            if article.excerpt != cleaned_excerpt:
                article.excerpt = cleaned_excerpt
                changed = True
            if article.content != cleaned_content:
                article.content = cleaned_content
                changed = True
            if article.image_caption != cleaned_caption:
                article.image_caption = cleaned_caption
                changed = True

            if not changed:
                continue

            article.normalized_title_hash = Article._hash_text(Article.normalize_title(article.title))
            article.content_hash = Article._hash_text(article.content or article.excerpt)
            article.word_count = len(article.content.split()) if article.content else 0
            article.paragraph_count = (
                len([part for part in article.content.split("\n\n") if part.strip()])
                if article.content
                else 0
            )
            if article.word_count > 0:
                article.read_time_minutes = max(1, article.word_count // 200)
            article.updated_at = timezone.now()

            updated += 1
            if dry_run:
                self.stdout.write(f"Would clean article {article.id}: {article.title[:80]}")
                continue

            batch.append(article)
            if len(batch) >= batch_size:
                self._flush(batch)
                batch = []

        if batch and not dry_run:
            self._flush(batch)

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"Done. Inspected: {inspected}. {action}: {updated}."))

    def _flush(self, articles):
        Article.objects.bulk_update(
            articles,
            [
                "title",
                "excerpt",
                "content",
                "image_caption",
                "normalized_title_hash",
                "content_hash",
                "word_count",
                "paragraph_count",
                "read_time_minutes",
                "updated_at",
            ],
        )
