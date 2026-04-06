from django.contrib import admin
from .models import URLIndex, BolognaProgram, ScrapedPage


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
