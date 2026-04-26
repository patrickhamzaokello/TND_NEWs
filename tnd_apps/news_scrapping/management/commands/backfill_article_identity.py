from django.core.management.base import BaseCommand

from tnd_apps.news_scrapping.models import Article


class Command(BaseCommand):
    help = 'Backfill canonical URLs and hash fields for existing articles.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=500)

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        qs = Article.objects.all().only('id', 'url', 'title', 'content', 'excerpt', 'external_id', 'source_published_id')
        updated = 0

        for article in qs.iterator(chunk_size=batch_size):
            article.canonical_url = Article.normalize_url(article.url)
            article.normalized_title_hash = Article._hash_text(Article.normalize_title(article.title))
            article.content_hash = Article._hash_text(article.content or article.excerpt)
            article.source_published_id = article.source_published_id or article.external_id or ''
            article.save(update_fields=[
                'canonical_url',
                'normalized_title_hash',
                'content_hash',
                'source_published_id',
                'updated_at',
            ])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f'Backfilled identity fields for {updated} articles.'))
