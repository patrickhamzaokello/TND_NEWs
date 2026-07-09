from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0008_articleenrichment_key_highlights'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailydigest',
            name='twitter_thread_id',
            field=models.CharField(
                blank=True,
                max_length=32,
                help_text='Tweet ID of the first tweet in the posted thread',
            ),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='twitter_posted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
