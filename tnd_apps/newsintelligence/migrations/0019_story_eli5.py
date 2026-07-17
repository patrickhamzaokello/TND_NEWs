from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0018_enrichment_card_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='storycluster',
            name='eli5_explanation',
            field=models.TextField(
                blank=True,
                help_text='On-demand "explain like I\'m 5" version, generated once and cached',
            ),
        ),
        migrations.AddField(
            model_name='storycluster',
            name='eli5_generated_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='storycluster',
            name='eli5_source_version',
            field=models.IntegerField(
                default=0,
                help_text='cluster.version at the time the ELI5 was generated — used to detect staleness',
            ),
        ),
    ]
