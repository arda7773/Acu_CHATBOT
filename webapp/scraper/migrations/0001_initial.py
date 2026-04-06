from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='URLIndex',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(max_length=500, unique=True)),
                ('title', models.CharField(blank=True, max_length=500)),
                ('path_keywords', models.TextField(blank=True, help_text='Keywords extracted from URL path')),
                ('category', models.CharField(blank=True, max_length=100)),
                ('indexed_at', models.DateTimeField(auto_now_add=True)),
                ('last_fetched', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'URL Index',
                'verbose_name_plural': 'URL Index',
                'ordering': ['url'],
            },
        ),
        migrations.CreateModel(
            name='ScrapedPage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(max_length=500, unique=True)),
                ('title', models.CharField(blank=True, max_length=500)),
                ('description', models.TextField(blank=True)),
                ('keywords', models.TextField(blank=True)),
                ('text', models.TextField()),
                ('lang', models.CharField(blank=True, max_length=10)),
                ('source', models.CharField(default='acibadem_main', max_length=50)),
                ('depth', models.IntegerField(default=0)),
                ('scraped_at', models.CharField(blank=True, max_length=50)),
            ],
            options={
                'verbose_name': 'Scrape Edilmiş Sayfa',
                'verbose_name_plural': 'Scrape Edilmiş Sayfalar',
                'ordering': ['depth', 'url'],
            },
        ),
        migrations.CreateModel(
            name='BolognaProgram',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(max_length=500, unique=True)),
                ('faculty', models.CharField(blank=True, max_length=300)),
                ('department', models.CharField(blank=True, max_length=300)),
                ('program_name', models.CharField(max_length=300)),
                ('content', models.TextField()),
                ('scraped_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Bologna Programı',
                'verbose_name_plural': 'Bologna Programları',
                'ordering': ['faculty', 'program_name'],
            },
        ),
    ]
