import json
import logging

from django.conf import settings as django_settings
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.urls import reverse

from .models import ChatSession, ChatMessage
from scraper.realtime_fetcher import get_context_for_question
from llm.ollama_client import get_answer

logger = logging.getLogger(__name__)


def _get_recent_sessions(current_session_id=None):
    """Return last 20 sessions that have at least one message."""
    sessions = (
        ChatSession.objects
        .prefetch_related('messages')
        .filter(messages__isnull=False)
        .distinct()
        .order_by('-created_at')[:20]
    )
    result = []
    for s in sessions:
        first_msg = s.messages.order_by('created_at').first()
        result.append({
            'id': str(s.id),
            'title': (first_msg.question[:60] + '…') if first_msg and len(first_msg.question) > 60 else (first_msg.question if first_msg else 'Sohbet'),
            'active': str(s.id) == str(current_session_id),
            'created_at': s.created_at,
        })
    return result


def chat_view(request, session_id=None):
    if session_id:
        # Navigating to a specific session via URL
        session, _ = ChatSession.objects.get_or_create(id=session_id)
        request.session['chat_session_id'] = str(session.id)
    else:
        sid = request.session.get('chat_session_id')
        if sid:
            session, _ = ChatSession.objects.get_or_create(id=sid)
        else:
            session = ChatSession.objects.create()
            request.session['chat_session_id'] = str(session.id)

    messages = session.messages.all()
    recent_sessions = _get_recent_sessions(session.id)

    return render(request, 'chat/index.html', {
        'messages': messages,
        'session_id': str(session.id),
        'ollama_model': django_settings.OLLAMA_MODEL,
        'recent_sessions': recent_sessions,
    })


@csrf_exempt
@require_http_methods(["POST"])
def chat_api(request):
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        session_id = data.get('session_id') or request.session.get('chat_session_id')

        if not question:
            return JsonResponse({'error': 'Soru boş olamaz.'}, status=400)

        if len(question) > 1000:
            return JsonResponse({'error': 'Soru çok uzun (max 1000 karakter).'}, status=400)

        if session_id:
            session, _ = ChatSession.objects.get_or_create(id=session_id)
        else:
            session = ChatSession.objects.create()
            request.session['chat_session_id'] = str(session.id)

        context, sources = get_context_for_question(question)
        answer = get_answer(question, context)

        message = ChatMessage.objects.create(
            session=session,
            question=question,
            answer=answer,
            sources=sources,
        )

        return JsonResponse({
            'answer': answer,
            'sources': sources,
            'session_id': str(session.id),
            'message_id': message.id,
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Geçersiz istek formatı.'}, status=400)
    except Exception as e:
        logger.error(f"Chat API error: {e}", exc_info=True)
        return JsonResponse({'error': 'Bir hata oluştu. Lütfen tekrar deneyin.'}, status=500)


def new_session(request):
    session = ChatSession.objects.create()
    request.session['chat_session_id'] = str(session.id)
    return redirect('chat')


@csrf_exempt
@require_http_methods(["POST"])
def delete_session(request, session_id):
    try:
        current_session_id = request.session.get('chat_session_id')
        session = ChatSession.objects.filter(id=session_id).first()
        if not session:
            return JsonResponse({'error': 'Sohbet bulunamadı.'}, status=404)

        is_active = str(session.id) == str(current_session_id)
        session.delete()

        response = {'success': True, 'deleted_session_id': str(session_id)}
        if is_active:
            new_session = ChatSession.objects.create()
            request.session['chat_session_id'] = str(new_session.id)
            response['redirect_url'] = reverse('chat')
            response['new_session_id'] = str(new_session.id)

        return JsonResponse(response)
    except Exception as e:
        logger.error(f"Delete session error: {e}", exc_info=True)
        return JsonResponse({'error': 'Sohbet silinemedi.'}, status=500)
