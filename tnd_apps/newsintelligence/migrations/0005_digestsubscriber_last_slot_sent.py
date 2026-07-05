from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0004_digest_subscribers'),
    ]

    operations = [
        migrations.AddField(
            model_name='digestsubscriber',
            name='last_slot_sent',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
    ]
