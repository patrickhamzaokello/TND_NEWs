from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0009_dailydigest_twitter_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailydigest',
            name='key_concern_short',
            field=models.CharField(
                max_length=200,
                blank=True,
                help_text='Tweet-sized version of key_concern (≤180 chars, complete sentence)',
            ),
        ),
    ]
