from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0002_intelligence_foundations'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='entitymention',
            index=models.Index(fields=['normalized_name', 'mention_date'], name='entity_ment_normali_241738_idx'),
        ),
        migrations.AddIndex(
            model_name='entitymention',
            index=models.Index(fields=['normalized_name', 'entity_type', 'mention_date'], name='entity_ment_normali_0dc0fc_idx'),
        ),
    ]
