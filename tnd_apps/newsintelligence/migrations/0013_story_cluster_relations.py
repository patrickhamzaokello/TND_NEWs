import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0012_semantic_story_engine'),
    ]

    operations = [
        migrations.CreateModel(
            name='StoryClusterRelation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('relation_type', models.CharField(
                    choices=[
                        ('continuation', 'Continuation — later development of the same saga'),
                        ('related', 'Related — connected but distinct event'),
                    ],
                    default='related', max_length=20,
                )),
                ('note', models.CharField(blank=True, max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('from_cluster', models.ForeignKey(
                    help_text='The newer story',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='outgoing_relations',
                    to='newsintelligence.storycluster',
                )),
                ('to_cluster', models.ForeignKey(
                    help_text='The earlier story it relates to',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='incoming_relations',
                    to='newsintelligence.storycluster',
                )),
            ],
            options={
                'db_table': 'story_cluster_relations',
                'unique_together': {('from_cluster', 'to_cluster')},
                'indexes': [
                    models.Index(fields=['from_cluster'], name='story_rel_from_idx'),
                    models.Index(fields=['to_cluster'], name='story_rel_to_idx'),
                ],
            },
        ),
    ]
