import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0011_image_generation_status'),
    ]

    operations = [
        # ArticleEnrichment — semantic embedding
        migrations.AddField(
            model_name='articleenrichment',
            name='embedding',
            field=models.JSONField(
                null=True, blank=True,
                help_text='Semantic vector (text-embedding-3-small, 1536 dims) of title+summary+entities',
            ),
        ),
        migrations.AddField(
            model_name='articleenrichment',
            name='embedded_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        # StoryCluster — centroid + synthesized content + versioning
        migrations.AddField(
            model_name='storycluster',
            name='centroid_embedding',
            field=models.JSONField(
                null=True, blank=True,
                help_text='Mean embedding of member articles — used for event detection',
            ),
        ),
        migrations.AddField(
            model_name='storycluster',
            name='short_summary',
            field=models.TextField(blank=True, help_text='2-3 sentence synthesized summary of the story so far'),
        ),
        migrations.AddField(
            model_name='storycluster',
            name='long_summary',
            field=models.TextField(blank=True, help_text='Full synthesized narrative combining all source reporting'),
        ),
        migrations.AddField(
            model_name='storycluster',
            name='key_highlights',
            field=models.JSONField(
                default=list, blank=True,
                help_text='Consensus facts across sources: [{"text", "sources_count"}]',
            ),
        ),
        migrations.AddField(
            model_name='storycluster',
            name='version',
            field=models.IntegerField(default=0, help_text='Incremented on each synthesis'),
        ),
        migrations.AddField(
            model_name='storycluster',
            name='synthesized_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='storycluster',
            name='articles_at_synthesis',
            field=models.IntegerField(
                default=0, help_text='Article count when last synthesized — used to detect growth',
            ),
        ),
        # StoryVersion — immutable synthesis snapshots
        migrations.CreateModel(
            name='StoryVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField()),
                ('title', models.CharField(max_length=300)),
                ('short_summary', models.TextField(blank=True)),
                ('long_summary', models.TextField(blank=True)),
                ('key_highlights', models.JSONField(blank=True, default=list)),
                ('article_count', models.IntegerField(default=0)),
                ('change_note', models.CharField(
                    blank=True, max_length=300,
                    help_text='What changed in this version (e.g. "3 new articles; title updated")',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('cluster', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='versions',
                    to='newsintelligence.storycluster',
                )),
            ],
            options={
                'db_table': 'story_versions',
                'ordering': ['-version'],
                'unique_together': {('cluster', 'version')},
                'indexes': [
                    models.Index(fields=['cluster', '-version'], name='story_ver_cluster_ver_idx'),
                ],
            },
        ),
    ]
