from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0006_articleenrichment_editorial_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailydigest',
            name='illustration',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='digest_illustrations/',
                help_text='AI-generated editorial illustration for this digest',
            ),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='illustration_caption',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='illustration_generated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
