from django.db import models
from pgvector.django import VectorField, HnswIndex


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


class ContentChunk(models.Model):
    """
    Semantic chunk of a scraped page or Bologna program.
    Each chunk has an embedding vector for pgvector similarity search.
    """
    SOURCE_SCRAPED = 'scraped'
    SOURCE_BOLOGNA = 'bologna'
    SOURCE_CHOICES = [
        (SOURCE_SCRAPED, 'ACU Web Sayfası'),
        (SOURCE_BOLOGNA, 'Bologna Programı'),
    ]

    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    source_url = models.URLField(max_length=500)
    title = models.CharField(max_length=500, blank=True)
    chunk_text = models.TextField()
    chunk_index = models.IntegerField(default=0)
    page_type = models.CharField(max_length=50, blank=True)
    section_type = models.CharField(max_length=50, blank=True)
    faculty = models.CharField(max_length=300, blank=True)
    department = models.CharField(max_length=300, blank=True)
    course_code = models.CharField(max_length=32, blank=True)
    language = models.CharField(max_length=10, blank=True)
    last_updated = models.DateTimeField(null=True, blank=True)
    is_stable = models.BooleanField(default=True)
    is_noisy = models.BooleanField(default=False)
    # nomic-embed-text produces 768-dim vectors
    embedding = VectorField(dimensions=768, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'İçerik Chunk'
        verbose_name_plural = 'İçerik Chunk\'ları'
        indexes = [
            HnswIndex(
                name='contentchunk_embedding_idx',
                fields=['embedding'],
                m=16,
                ef_construction=64,
                opclasses=['vector_cosine_ops'],
            )
        ]

    def __str__(self):
        return f"[{self.source_type}] {self.title} (chunk {self.chunk_index})"
