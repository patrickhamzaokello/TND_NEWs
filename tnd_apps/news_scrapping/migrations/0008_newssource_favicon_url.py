from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('news_scrapping', '0007_source_reliability_article_identity'),
    ]

    operations = [
        migrations.AddField(
            model_name='newssource',
            name='favicon_url',
            field=models.URLField(blank=True, max_length=500),
        ),
    ]
