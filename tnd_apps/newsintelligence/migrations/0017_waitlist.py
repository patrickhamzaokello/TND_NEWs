from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('newsintelligence', '0016_subscriber_evening_frequency'),
    ]

    operations = [
        migrations.CreateModel(
            name='WaitlistEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('name', models.CharField(blank=True, max_length=120)),
                ('interest', models.CharField(
                    blank=True, max_length=30,
                    choices=[
                        ('reader', 'Staying informed'),
                        ('professional', 'Work / research'),
                        ('developer', 'API access'),
                        ('other', 'Other'),
                    ],
                    help_text='What they want the platform for',
                )),
                ('referrer', models.CharField(blank=True, max_length=300, help_text='HTTP referrer at signup')),
                ('invited', models.BooleanField(default=False)),
                ('invited_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'waitlist_entries',
                'ordering': ['-created_at'],
            },
        ),
    ]
