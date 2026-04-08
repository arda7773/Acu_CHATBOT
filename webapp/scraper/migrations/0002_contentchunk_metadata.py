from django.db import migrations, models
from pgvector.django import VectorField


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='ContentChunk',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('source_type', models.CharField(choices=[('scraped', 'ACU Web Sayfası'), ('bologna', 'Bologna Programı')], max_length=20)),
                        ('source_url', models.URLField(max_length=500)),
                        ('title', models.CharField(blank=True, max_length=500)),
                        ('chunk_text', models.TextField()),
                        ('chunk_index', models.IntegerField(default=0)),
                        ('embedding', VectorField(dimensions=768, null=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                    ],
                    options={
                        'verbose_name': 'İçerik Chunk',
                        'verbose_name_plural': "İçerik Chunk'ları",
                    },
                ),
            ],
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='course_code',
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='department',
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='faculty',
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='is_noisy',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='is_stable',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='language',
            field=models.CharField(blank=True, max_length=10),
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='last_updated',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='page_type',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='contentchunk',
            name='section_type',
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
