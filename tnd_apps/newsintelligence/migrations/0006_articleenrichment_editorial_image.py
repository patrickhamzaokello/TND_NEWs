from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0005_digestsubscriber_last_slot_sent'),
    ]

    operations = [
        migrations.AddField(
            model_name='articleenrichment',
            name='editorial_image',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='editorial_images/',
                help_text='AI-generated editorial engraving version of the featured image',
            ),
        ),
        migrations.AddField(
            model_name='articleenrichment',
            name='editorial_image_generated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
