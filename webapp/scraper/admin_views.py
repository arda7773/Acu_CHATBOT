from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from chat.models import ChatMessage, ChatSession
from scraper.models import BolognaProgram, ContentChunk, ScrapedPage, URLIndex


@staff_member_required
def system_stats_view(request):
    stats = [
        {
            'label': 'Toplam Chat Oturumu',
            'value': ChatSession.objects.count(),
        },
        {
            'label': 'Toplam Chat Mesajı',
            'value': ChatMessage.objects.count(),
        },
        {
            'label': 'İndekslenen URL',
            'value': URLIndex.objects.count(),
        },
        {
            'label': 'Scraped Page',
            'value': ScrapedPage.objects.count(),
        },
        {
            'label': 'Bologna Programı',
            'value': BolognaProgram.objects.count(),
        },
        {
            'label': 'Content Chunk',
            'value': ContentChunk.objects.count(),
        },
        {
            'label': 'Embedding Hazır Chunk',
            'value': ContentChunk.objects.filter(embedding__isnull=False).count(),
        },
        {
            'label': 'Gürültülü İşaretlenen Chunk',
            'value': ContentChunk.objects.filter(is_noisy=True).count(),
        },
    ]

    recent = {
        'last_chat_message': ChatMessage.objects.order_by('-created_at').first(),
        'last_scraped_page': ScrapedPage.objects.order_by('-id').first(),
        'last_bologna_program': BolognaProgram.objects.order_by('-scraped_at').first(),
        'last_chunk': ContentChunk.objects.order_by('-created_at').first(),
    }

    return render(request, 'admin/system_stats.html', {
        'title': 'System Stats',
        'stats': stats,
        'recent': recent,
    })
