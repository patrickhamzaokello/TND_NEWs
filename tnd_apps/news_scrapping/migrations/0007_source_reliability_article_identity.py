import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('news_scrapping', '0006_usernotification_articlenotificationhistory_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='newssource',
            name='country',
            field=models.CharField(default='Uganda', max_length=80),
        ),
        migrations.AddField(
            model_name='newssource',
            name='editorial_notes',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='newssource',
            name='failure_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='newssource',
            name='language',
            field=models.CharField(default='English', max_length=40),
        ),
        migrations.AddField(
            model_name='newssource',
            name='last_successful_scrape_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='newssource',
            name='ownership',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='newssource',
            name='reliability_tier',
            field=models.CharField(choices=[('high', 'High'), ('medium', 'Medium'), ('low', 'Low'), ('unknown', 'Unknown')], default='unknown', max_length=20),
        ),
        migrations.AddField(
            model_name='newssource',
            name='scrape_config',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='article',
            name='canonical_url',
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='article',
            name='content_hash',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='article',
            name='last_scrape_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='article',
            name='normalized_title_hash',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='article',
            name='scrape_status',
            field=models.CharField(choices=[('pending', 'Pending'), ('partial', 'Partial'), ('complete', 'Complete'), ('failed', 'Failed')], default='pending', max_length=20),
        ),
        migrations.AddField(
            model_name='article',
            name='search_vector',
            field=django.contrib.postgres.search.SearchVectorField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='article',
            name='source_published_id',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['canonical_url'], name='articles_canonic_406789_idx'),
        ),
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['source', 'source_published_id'], name='articles_source__09e420_idx'),
        ),
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['content_hash'], name='articles_content_a6c826_idx'),
        ),
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['normalized_title_hash'], name='articles_normali_a93521_idx'),
        ),
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['source', '-published_at'], name='articles_source__94fb86_idx'),
        ),
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['category', '-published_at'], name='articles_categor_7af3ee_idx'),
        ),
        migrations.AddIndex(
            model_name='article',
            index=models.Index(fields=['has_full_content', '-scraped_at'], name='articles_has_ful_4c1f98_idx'),
        ),
        migrations.AddIndex(
            model_name='article',
            index=django.contrib.postgres.indexes.GinIndex(fields=['search_vector'], name='articles_search__3b8987_gin'),
        ),
    ]
