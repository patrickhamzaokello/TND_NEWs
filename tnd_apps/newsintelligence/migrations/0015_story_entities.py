from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0014_story_overview'),
    ]

    operations = [
        migrations.AddField(
            model_name='storycluster',
            name='entities',
            field=models.JSONField(
                default=list, blank=True,
                help_text='Entities appearing verbatim in synthesized text: [{"name", "type"}] — clients substring-match to render clickable tags',
            ),
        ),
    ]
