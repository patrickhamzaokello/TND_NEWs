from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('news_scrapping', '0008_newssource_favicon_url'),
    ]

    operations = [
        migrations.AlterField(
            model_name='category',
            name='slug',
            field=models.SlugField(max_length=120, unique=True),
        ),
        migrations.AlterField(
            model_name='tag',
            name='slug',
            field=models.SlugField(max_length=120, unique=True),
        ),
        migrations.AlterField(
            model_name='article',
            name='external_id',
            field=models.CharField(db_index=True, max_length=120),
        ),
    ]
