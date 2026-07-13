from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0015_story_entities'),
    ]

    operations = [
        migrations.AlterField(
            model_name='digestsubscriber',
            name='frequency',
            field=models.CharField(
                max_length=20,
                default='morning_evening',
                choices=[
                    ('morning_evening', 'Morning digest + evening articles (default)'),
                    ('daily', 'Morning digest only'),
                    ('evening', 'Evening roundup only'),
                    ('breaking', 'Breaking news only'),
                ],
            ),
        ),
    ]
