from django.contrib.postgres.search import SearchVector
from django.core.management.base import BaseCommand

from tnd_apps.news_scrapping.models import Article


class Command(BaseCommand):
    help = 'Refresh PostgreSQL full-text search vectors for articles.'

    def handle(self, *args, **options):
        updated = Article.objects.update(
            search_vector=(
                SearchVector('title', weight='A') +
                SearchVector('excerpt', weight='B') +
                SearchVector('content', weight='C')
            )
        )
        self.stdout.write(self.style.SUCCESS(f'Updated search vectors for {updated} articles.'))
