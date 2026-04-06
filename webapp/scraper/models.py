from django.db import models


class URLIndex(models.Model):
    url = models.URLField(max_length=500, unique=True)
    title = models.CharField(max_length=500, blank=True)
    path_keywords = models.TextField(blank=True, help_text='Keywords extracted from URL path')
    category = models.CharField(max_length=100, blank=True)
    indexed_at = models.DateTimeField(auto_now_add=True)
    last_fetched = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'URL Index'
        verbose_name_plural = 'URL Index'
        ordering = ['url']

    def __str__(self):
        return self.title or self.url


class ScrapedPage(models.Model):
    url = models.URLField(max_length=500, unique=True)
    title = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    keywords = models.TextField(blank=True)
    text = models.TextField()
    lang = models.CharField(max_length=10, blank=True)
    source = models.CharField(max_length=50, default='acibadem_main')
    depth = models.IntegerField(default=0)
    scraped_at = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = 'Scrape Edilmiş Sayfa'
        verbose_name_plural = 'Scrape Edilmiş Sayfalar'
        ordering = ['depth', 'url']

    def __str__(self):
        return self.title or self.url


class BolognaProgram(models.Model):
    url = models.URLField(max_length=500, unique=True)
    faculty = models.CharField(max_length=300, blank=True)
    department = models.CharField(max_length=300, blank=True)
    program_name = models.CharField(max_length=300)
    content = models.TextField()
    scraped_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Bologna Programı'
        verbose_name_plural = 'Bologna Programları'
        ordering = ['faculty', 'program_name']

    def __str__(self):
        return f"{self.faculty} - {self.program_name}"
