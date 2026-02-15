from django.apps import AppConfig


class NewsEnrichmentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tnd_apps.newsintelligence'
    verbose_name = 'News Enrichment'

    def ready(self):
        pass
