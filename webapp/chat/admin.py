from django.contrib import admin
from .models import ChatSession, ChatMessage


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ['question', 'answer', 'sources', 'created_at']
    can_delete = False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'created_at', 'message_count']
    readonly_fields = ['id', 'created_at']
    inlines = [ChatMessageInline]

    def message_count(self, obj):
        return obj.messages.count()
    message_count.short_description = 'Mesaj Sayısı'


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'question_preview', 'session', 'created_at']
    readonly_fields = ['session', 'question', 'answer', 'sources', 'created_at']
    search_fields = ['question', 'answer']
    list_filter = ['created_at']
    date_hierarchy = 'created_at'

    def question_preview(self, obj):
        return obj.question[:80]
    question_preview.short_description = 'Soru'
