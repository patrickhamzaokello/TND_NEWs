from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0017_waitlist'),
    ]

    operations = [
        migrations.AddField(
            model_name='articleenrichment',
            name='neutral_title',
            field=models.CharField(
                blank=True, max_length=300,
                help_text='Rewritten neutral headline — used as the story card title for single-article stories',
            ),
        ),
        migrations.AddField(
            model_name='articleenrichment',
            name='why_it_matters',
            field=models.TextField(
                blank=True,
                help_text='One dense sentence of concrete stakes — used on story cards',
            ),
        ),
    ]
