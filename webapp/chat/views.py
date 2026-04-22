import json
import logging

from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.conf import settings as django_settings
from django.db.models import Max
from django.shortcuts import render, redirect
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.urls import reverse

from .models import ChatSession, ChatMessage
from scraper.realtime_fetcher import get_context_for_question
from llm.ollama_client import get_answer, stream_answer

logger = logging.getLogger(__name__)


def _get_recent_sessions(user, current_session_id=None):
    """Return last 20 sessions that have at least one message."""
    sessions = (
        ChatSession.objects
        .prefetch_related('messages')
        .annotate(last_message_at=Max('messages__created_at'))
        .filter(owner=user)
        .filter(messages__isnull=False)
        .distinct()
        .order_by('-last_message_at', '-created_at')[:20]
    )
    result = []
    for s in sessions:
        first_msg = s.messages.order_by('created_at').first()
        result.append({
            'id': str(s.id),
            'title': (first_msg.question[:60] + '…') if first_msg and len(first_msg.question) > 60 else (first_msg.question if first_msg else 'Sohbet'),
            'active': str(s.id) == str(current_session_id),
            'created_at': s.created_at,
            'last_message_at': s.last_message_at,
        })
    return result


def _create_user_session(request):
    session = ChatSession.objects.create(owner=request.user)
    request.session['chat_session_id'] = str(session.id)
    return session


def _resolve_session_for_request(request, session_id=None):
    if session_id:
        session = ChatSession.objects.filter(id=session_id, owner=request.user).first()
        if session:
            request.session['chat_session_id'] = str(session.id)
            return session
    return _create_user_session(request)


def _get_user_session(request, session_id=None):
    if session_id:
        session = ChatSession.objects.filter(id=session_id, owner=request.user).first()
        if session:
            request.session['chat_session_id'] = str(session.id)
            return session
        return _create_user_session(request)

    sid = request.session.get('chat_session_id')
    if sid:
        session = ChatSession.objects.filter(id=sid, owner=request.user).first()
        if session:
            return session

    return _create_user_session(request)


def _validate_question(question: str):
    if not question:
        return JsonResponse({'error': 'Soru boş olamaz.'}, status=400)
    if len(question) > 1000:
        return JsonResponse({'error': 'Soru çok uzun (max 1000 karakter).'}, status=400)
    return None


def _serialize_done_payload(session, message):
    return {
        'answer': message.answer,
        'sources': message.sources,
        'session_id': str(session.id),
        'message_id': message.id,
    }


def _stream_message_answer(session, question: str, *, message: ChatMessage | None = None, deleted_message_ids: list[int] | None = None):
    context, sources = get_context_for_question(question)

    def event_stream():
        full_answer: list[str] = []
        try:
            for piece in stream_answer(question, context):
                full_answer.append(piece)
                payload = json.dumps({'text': piece})
                yield f"event: chunk\ndata: {payload}\n\n"

            answer = ''.join(full_answer).strip()
            if message is None:
                saved_message = ChatMessage.objects.create(
                    session=session,
                    question=question,
                    answer=answer,
                    sources=sources,
                )
            else:
                message.question = question
                message.answer = answer
                message.sources = sources
                message.save(update_fields=['question', 'answer', 'sources'])
                saved_message = message

            done_payload = _serialize_done_payload(session, saved_message)
            if deleted_message_ids:
                done_payload['deleted_message_ids'] = deleted_message_ids
            yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"
        except Exception as exc:
            logger.error("Streaming chat API error: %s", exc, exc_info=True)
            error_payload = json.dumps({'error': 'Bir hata oluştu. Lütfen tekrar deneyin.'})
            yield f"event: error\ndata: {error_payload}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


def login_view(request):
    if request.user.is_authenticated:
        return redirect('chat')

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect(request.POST.get('next') or request.GET.get('next') or reverse('chat'))

    return render(request, 'auth/login.html', {'form': form})


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('chat')

    form = UserCreationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect('chat')

    return render(request, 'auth/signup.html', {'form': form})


@require_http_methods(["POST"])
def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def chat_view(request, session_id=None):
    session = _get_user_session(request, session_id=session_id)

    messages = session.messages.all()
    recent_sessions = _get_recent_sessions(request.user, session.id)

    return render(request, 'chat/index.html', {
        'messages': messages,
        'session_id': str(session.id),
        'ollama_model': django_settings.OLLAMA_MODEL,
        'recent_sessions': recent_sessions,
    })


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def chat_api(request):
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        session_id = data.get('session_id') or request.session.get('chat_session_id')

        validation_error = _validate_question(question)
        if validation_error:
            return validation_error

        session = _resolve_session_for_request(request, session_id)

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


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def chat_api_stream(request):
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        session_id = data.get('session_id') or request.session.get('chat_session_id')

        validation_error = _validate_question(question)
        if validation_error:
            return validation_error

        session = _resolve_session_for_request(request, session_id)
        return _stream_message_answer(session, question)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Geçersiz istek formatı.'}, status=400)
    except Exception as e:
        logger.error(f"Streaming chat API setup error: {e}", exc_info=True)
        return JsonResponse({'error': 'Bir hata oluştu. Lütfen tekrar deneyin.'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def edit_message_stream(request, message_id):
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        session_id = data.get('session_id') or request.session.get('chat_session_id')

        validation_error = _validate_question(question)
        if validation_error:
            return validation_error

        session = ChatSession.objects.filter(id=session_id, owner=request.user).first()
        if not session:
            return JsonResponse({'error': 'Sohbet bulunamadı.'}, status=404)

        message = ChatMessage.objects.filter(id=message_id, session=session).first()
        if not message:
            return JsonResponse({'error': 'Düzenlenecek mesaj bulunamadı.'}, status=404)

        deleted_message_ids = list(
            session.messages.filter(id__gt=message.id).values_list('id', flat=True)
        )
        if deleted_message_ids:
            session.messages.filter(id__in=deleted_message_ids).delete()

        return _stream_message_answer(
            session,
            question,
            message=message,
            deleted_message_ids=deleted_message_ids,
        )
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Geçersiz istek formatı.'}, status=400)
    except Exception as exc:
        logger.error("Edit chat API error: %s", exc, exc_info=True)
        return JsonResponse({'error': 'Bir hata oluştu. Lütfen tekrar deneyin.'}, status=500)


@login_required
def new_session(request):
    session = _create_user_session(request)
    return redirect('chat')


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def delete_session(request, session_id):
    try:
        current_session_id = request.session.get('chat_session_id')
        session = ChatSession.objects.filter(id=session_id, owner=request.user).first()
        if not session:
            return JsonResponse({'error': 'Sohbet bulunamadı.'}, status=404)

        is_active = str(session.id) == str(current_session_id)
        session.delete()

        response = {'success': True, 'deleted_session_id': str(session_id)}
        if is_active:
            new_session = _create_user_session(request)
            response['redirect_url'] = reverse('chat')
            response['new_session_id'] = str(new_session.id)

        return JsonResponse(response)
    except Exception as e:
        logger.error(f"Delete session error: {e}", exc_info=True)
        return JsonResponse({'error': 'Sohbet silinemedi.'}, status=500)
