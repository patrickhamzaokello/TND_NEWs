from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0013_story_cluster_relations'),
    ]

    operations = [
        migrations.AddField(
            model_name='storycluster',
            name='overview',
            field=models.TextField(
                blank=True,
                help_text='Broader context: why this matters, historical background, related events',
            ),
        ),
        migrations.AddField(
            model_name='storyversion',
            name='overview',
            field=models.TextField(blank=True),
        ),
    ]
