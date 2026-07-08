from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0007_dailydigest_illustration'),
    ]

    operations = [
        migrations.AddField(
            model_name='articleenrichment',
            name='key_highlights',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Phrases from the article body that clients should underline. '
                    'Each item: {text, type, url?} where type is fact | figure | claim | link'
                ),
            ),
        ),
    ]
