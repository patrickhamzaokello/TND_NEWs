import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('news_scrapping', '0007_source_reliability_article_identity'),
        ('newsintelligence', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='articleenrichment',
            name='bias_or_framing_notes',
            field=models.JSONField(blank=True, default=list, help_text='Observed framing or perspective notes for source-aware analysis'),
        ),
        migrations.AddField(
            model_name='articleenrichment',
            name='citations',
            field=models.JSONField(blank=True, default=list, help_text='Source references used by AI outputs'),
        ),
        migrations.AddField(
            model_name='articleenrichment',
            name='claims',
            field=models.JSONField(blank=True, default=list, help_text='Grounded claims with evidence and confidence'),
        ),
        migrations.AddField(
            model_name='articleenrichment',
            name='local_impact',
            field=models.JSONField(blank=True, default=dict, help_text='Uganda/local impact analysis by region, group, and time horizon'),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='citations',
            field=models.JSONField(blank=True, default=list, help_text='Source references used in digest_text and story summaries'),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='editorial_review_status',
            field=models.CharField(choices=[('draft', 'Draft'), ('needs_review', 'Needs Review'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='needs_review', max_length=20),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='dailydigest',
            name='reviewed_by',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='entitymention',
            name='normalized_name',
            field=models.CharField(blank=True, db_index=True, max_length=200),
        ),
        migrations.AddField(
            model_name='entitymention',
            name='salience',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='Entity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('normalized_name', models.CharField(db_index=True, max_length=200)),
                ('entity_type', models.CharField(choices=[('person', 'Person'), ('organization', 'Organization'), ('location', 'Location')], max_length=20)),
                ('aliases', models.JSONField(blank=True, default=list)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'entities',
                'unique_together': {('normalized_name', 'entity_type')},
            },
        ),
        migrations.CreateModel(
            name='StoryCluster',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=300)),
                ('slug', models.SlugField(max_length=320, unique=True)),
                ('summary', models.TextField(blank=True)),
                ('why_this_matters', models.TextField(blank=True)),
                ('local_impact', models.JSONField(blank=True, default=dict)),
                ('primary_theme', models.CharField(blank=True, max_length=80)),
                ('status', models.CharField(choices=[('active', 'Active'), ('dormant', 'Dormant'), ('resolved', 'Resolved')], default='active', max_length=20)),
                ('importance_score', models.IntegerField(default=0)),
                ('first_seen_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('last_seen_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'story_clusters',
                'ordering': ['-last_seen_at', '-importance_score'],
            },
        ),
        migrations.CreateModel(
            name='ArticleClaim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('claim_text', models.TextField()),
                ('evidence_text', models.TextField(blank=True)),
                ('confidence', models.FloatField(default=0.0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('article', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='claims', to='news_scrapping.article')),
                ('enrichment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='newsintelligence.articleenrichment')),
            ],
            options={'db_table': 'article_claims'},
        ),
        migrations.CreateModel(
            name='ArticleCitation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(max_length=500)),
                ('title', models.CharField(max_length=500)),
                ('source_name', models.CharField(blank=True, max_length=120)),
                ('evidence_text', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('article', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='citations', to='news_scrapping.article')),
                ('enrichment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='newsintelligence.articleenrichment')),
            ],
            options={'db_table': 'article_citations'},
        ),
        migrations.CreateModel(
            name='StoryClusterArticle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('relevance_score', models.FloatField(default=1.0)),
                ('perspective_label', models.CharField(blank=True, max_length=120)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('article', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='story_cluster_links', to='news_scrapping.article')),
                ('cluster', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cluster_articles', to='newsintelligence.storycluster')),
            ],
            options={
                'db_table': 'story_cluster_articles',
                'unique_together': {('cluster', 'article')},
            },
        ),
        migrations.CreateModel(
            name='StoryTimelineEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_date', models.DateTimeField()),
                ('title', models.CharField(max_length=240)),
                ('description', models.TextField()),
                ('citations', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('article', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='news_scrapping.article')),
                ('cluster', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='timeline_events', to='newsintelligence.storycluster')),
            ],
            options={
                'db_table': 'story_timeline_events',
                'ordering': ['event_date'],
            },
        ),
        migrations.CreateModel(
            name='SourcePerspective',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('framing_summary', models.TextField(blank=True)),
                ('notable_emphasis', models.JSONField(blank=True, default=list)),
                ('omitted_context', models.JSONField(blank=True, default=list)),
                ('sentiment_score', models.FloatField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('article', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='news_scrapping.article')),
                ('cluster', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='source_perspectives', to='newsintelligence.storycluster')),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='news_scrapping.newssource')),
            ],
            options={
                'db_table': 'source_perspectives',
                'unique_together': {('cluster', 'source', 'article')},
            },
        ),
        migrations.CreateModel(
            name='StoryAlert',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=240)),
                ('reason', models.TextField()),
                ('importance_score', models.IntegerField(default=0)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('sent', 'Sent'), ('suppressed', 'Suppressed')], default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('article', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='news_scrapping.article')),
                ('cluster', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alerts', to='newsintelligence.storycluster')),
            ],
            options={'db_table': 'story_alerts'},
        ),
        migrations.AddIndex(
            model_name='dailydigest',
            index=models.Index(fields=['is_published', '-digest_date'], name='daily_diges_is_publ_6093ce_idx'),
        ),
        migrations.AddIndex(
            model_name='dailydigest',
            index=models.Index(fields=['editorial_review_status', '-digest_date'], name='daily_diges_editori_bc6e6f_idx'),
        ),
        migrations.AddIndex(
            model_name='entity',
            index=models.Index(fields=['entity_type', 'normalized_name'], name='entities_entity__21d934_idx'),
        ),
        migrations.AddIndex(
            model_name='storycluster',
            index=models.Index(fields=['status', '-last_seen_at'], name='story_clust_status_ccf52d_idx'),
        ),
        migrations.AddIndex(
            model_name='storycluster',
            index=models.Index(fields=['primary_theme', '-last_seen_at'], name='story_clust_primary_4fbb8d_idx'),
        ),
        migrations.AddIndex(
            model_name='storycluster',
            index=models.Index(fields=['importance_score'], name='story_clust_importa_8e5c2f_idx'),
        ),
        migrations.AddIndex(
            model_name='articleclaim',
            index=models.Index(fields=['article'], name='article_cla_article_600388_idx'),
        ),
        migrations.AddIndex(
            model_name='articleclaim',
            index=models.Index(fields=['confidence'], name='article_cla_confide_7b1749_idx'),
        ),
        migrations.AddIndex(
            model_name='articlecitation',
            index=models.Index(fields=['article'], name='article_cit_article_0dd6ad_idx'),
        ),
        migrations.AddIndex(
            model_name='articlecitation',
            index=models.Index(fields=['source_name'], name='article_cit_source__f9eada_idx'),
        ),
        migrations.AddIndex(
            model_name='storyclusterarticle',
            index=models.Index(fields=['cluster', '-relevance_score'], name='story_clust_cluster_51d232_idx'),
        ),
        migrations.AddIndex(
            model_name='storyclusterarticle',
            index=models.Index(fields=['article'], name='story_clust_article_ca3ef9_idx'),
        ),
        migrations.AddIndex(
            model_name='storytimelineevent',
            index=models.Index(fields=['cluster', 'event_date'], name='story_timel_cluster_1d3c5c_idx'),
        ),
        migrations.AddIndex(
            model_name='sourceperspective',
            index=models.Index(fields=['cluster', 'source'], name='source_pers_cluster_55f0ce_idx'),
        ),
        migrations.AddIndex(
            model_name='storyalert',
            index=models.Index(fields=['status', '-created_at'], name='story_alert_status_a4a88e_idx'),
        ),
        migrations.AddIndex(
            model_name='storyalert',
            index=models.Index(fields=['cluster', '-created_at'], name='story_alert_cluster_6285ee_idx'),
        ),
    ]
