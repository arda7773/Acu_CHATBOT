from django.contrib import admin
from .models import URLIndex, BolognaProgram, ScrapedPage, ContentChunk


@admin.register(URLIndex)
class URLIndexAdmin(admin.ModelAdmin):
    list_display = ['url_preview', 'title', 'category', 'indexed_at', 'last_fetched']
    search_fields = ['url', 'title', 'path_keywords']
    list_filter = ['category', 'indexed_at']
    readonly_fields = ['indexed_at', 'last_fetched']

    def url_preview(self, obj):
        return obj.url[:80]
    url_preview.short_description = 'URL'


@admin.register(ScrapedPage)
class ScrapedPageAdmin(admin.ModelAdmin):
    list_display = ['title', 'url_preview', 'lang', 'depth', 'scraped_at']
    search_fields = ['title', 'url', 'text', 'description']
    list_filter = ['lang', 'depth', 'source']
    readonly_fields = ['url', 'scraped_at']

    def url_preview(self, obj):
        return obj.url[:70]
    url_preview.short_description = 'URL'


@admin.register(BolognaProgram)
class BolognaProgramAdmin(admin.ModelAdmin):
    list_display = ['program_name', 'faculty', 'department', 'scraped_at']
    search_fields = ['program_name', 'faculty', 'department', 'content']
    list_filter = ['faculty', 'scraped_at']
    readonly_fields = ['scraped_at']


@admin.register(ContentChunk)
class ContentChunkAdmin(admin.ModelAdmin):
    list_display = [
        'title_preview',
        'source_type',
        'page_type',
        'faculty',
        'department',
        'chunk_index',
        'has_embedding',
        'created_at',
    ]
    search_fields = [
        'title',
        'source_url',
        'chunk_text',
        'faculty',
        'department',
        'course_code',
    ]
    list_filter = [
        'source_type',
        'page_type',
        'section_type',
        'faculty',
        'department',
        'language',
        'is_stable',
        'is_noisy',
        'created_at',
    ]
    readonly_fields = [
        'source_type',
        'source_url',
        'title',
        'chunk_text',
        'chunk_index',
        'page_type',
        'section_type',
        'faculty',
        'department',
        'course_code',
        'language',
        'last_updated',
        'is_stable',
        'is_noisy',
        'embedding',
        'created_at',
    ]
    ordering = ['-created_at']

    def title_preview(self, obj):
        return obj.title[:80] if obj.title else obj.source_url[:80]
    title_preview.short_description = 'Başlık'

    def has_embedding(self, obj):
        return bool(obj.embedding)
    has_embedding.boolean = True
    has_embedding.short_description = 'Embedding'
