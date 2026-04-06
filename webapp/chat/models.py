import uuid
from django.db import models


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Chat Oturumu'
        verbose_name_plural = 'Chat Oturumları'

    def __str__(self):
        return f"Oturum {str(self.id)[:8]} - {self.created_at.strftime('%d.%m.%Y %H:%M')}"

    def message_count(self):
        return self.messages.count()


class ChatMessage(models.Model):
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    question = models.TextField(verbose_name='Soru')
    answer = models.TextField(verbose_name='Cevap')
    sources = models.JSONField(default=list, verbose_name='Kaynaklar')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Chat Mesajı'
        verbose_name_plural = 'Chat Mesajları'

    def __str__(self):
        return f"{self.question[:60]}..."
