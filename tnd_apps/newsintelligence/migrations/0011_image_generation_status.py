from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0010_dailydigest_key_concern_short'),
    ]

    operations = [
        # ArticleEnrichment — editorial image tracking
        migrations.AddField(
            model_name='articleenrichment',
            name='editorial_image_last_attempt',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='articleenrichment',
            name='editorial_image_status',
            field=models.CharField(
                max_length=20, blank=True,
                choices=[
                    ('generated', 'Generated'),
                    ('skipped', 'Skipped — no source image'),
                    ('moderation', 'Blocked by moderation'),
                    ('download_error', 'Source image download failed'),
                    ('api_error', 'OpenAI API error'),
                    ('error', 'Unexpected error'),
                ],
                help_text='Result of the last generation attempt',
            ),
        ),
        migrations.AddField(
            model_name='articleenrichment',
            name='editorial_image_error',
            field=models.TextField(blank=True, help_text='Error detail from last attempt'),
        ),
        # DailyDigest — illustration tracking
        migrations.AddField(
            model_name='dailydigest',
            name='illustration_last_attempt',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='illustration_status',
            field=models.CharField(
                max_length=20, blank=True,
                choices=[
                    ('generated', 'Generated'),
                    ('skipped', 'Skipped — no top story'),
                    ('moderation', 'Blocked by moderation'),
                    ('download_error', 'Source image download failed'),
                    ('api_error', 'OpenAI API error'),
                    ('error', 'Unexpected error'),
                ],
                help_text='Result of the last illustration generation attempt',
            ),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='illustration_error',
            field=models.TextField(blank=True, help_text='Error detail from last attempt'),
        ),
    ]
