import json
import logging

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import ChatSession, ChatMessage
from scraper.realtime_fetcher import get_context_for_question
from llm.ollama_client import get_answer

logger = logging.getLogger(__name__)


def chat_view(request):
    session_id = request.session.get('chat_session_id')
    if session_id:
        session, _ = ChatSession.objects.get_or_create(id=session_id)
    else:
        session = ChatSession.objects.create()
        request.session['chat_session_id'] = str(session.id)

    messages = session.messages.all()
    return render(request, 'chat/index.html', {
        'messages': messages,
        'session_id': str(session.id),
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

        # Fetch relevant context from ACU website + Bologna DB
        context, sources = get_context_for_question(question)

        # Get answer from Ollama llama3.2:3b
        answer = get_answer(question, context)

        # Save to database
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
        return JsonResponse({
            'error': 'Bir hata oluştu. Lütfen tekrar deneyin.',
        }, status=500)


def new_session(request):
    """Start a new chat session."""
    session = ChatSession.objects.create()
    request.session['chat_session_id'] = str(session.id)
    from django.shortcuts import redirect
    return redirect('chat')
