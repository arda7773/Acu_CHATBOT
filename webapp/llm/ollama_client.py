import json
import logging
import re
from collections.abc import Iterator

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen Acıbadem Üniversitesi'nin resmi yapay zeka asistanısın.

ZORUNLU KURALLAR — İstisna Kabul Etmez:
1. Yanıtlarını YALNIZCA aşağıdaki BAĞLAM bloğundaki bilgilere dayandır.
2. Bağlamda açıkça yazılmayan hiçbir şeyi UYDURMA, TAHMİN ETME veya kendi bilginden TAMAMLAMA.
3. Bağlamda ilgili bilgi yoksa SADECE şu cümleyi yaz, başka hiçbir şey ekleme:
   "Bu konuda elimde yeterli bilgi bulunmuyor. Daha fazla bilgi için https://www.acibadem.edu.tr adresini ziyaret edebilir veya üniversiteyle iletişime geçebilirsiniz."
4. Kullanıcı Türkçe yazıyorsa Türkçe, İngilizce yazıyorsa İngilizce yanıt ver.
5. Liste soruları için bağlamdaki TÜM öğeleri eksiksiz listele.
6. Bağlamda kısmi bilgi varsa "Elimdeki bilgilere göre..." diye başla ve neyi bilmediğini belirt.
7. Türkçe yanıtta İngilizce kelime karıştırma, bozuk veya anlamsız cümle kurma.
8. Sayılar, tarihler, isimler: YALNIZCA bağlamda geçen değerleri kullan, asla tahmin etme.
9. Kullanıcı adresi, konumu veya "nerede/nerde" bilgisini soruyorsa ve bağlamda açık adres varsa, genel konum özeti verme; açık adresi aynen ve doğrudan yaz."""

FALLBACK_ANSWER = (
    "Bu konuda elimde yeterli bilgi bulunmuyor. Daha fazla bilgi için "
    "https://www.acibadem.edu.tr adresini ziyaret edebilir veya üniversiteyle iletişime geçebilirsiniz."
)


def build_context_fallback(context: str) -> str:
    lines = [line.strip() for line in context.splitlines() if line.strip()]
    clean_lines = []
    for line in lines:
        if line.startswith('==='):
            continue
        if len(line) < 20:
            continue
        lowered = line.lower()
        if any(noisy in lowered for noisy in (
            'tanıtım kataloğu', 'tanitim katalogu', 'sanal tur',
            'başlangıç tarihi', 'baslangic tarihi', 'bitiş tarihi', 'bitis tarihi',
        )):
            continue
        clean_lines.append(line)
        if len(clean_lines) >= 4:
            break

    if not clean_lines:
        return FALLBACK_ANSWER

    summary = ' '.join(clean_lines[:3])
    return f"Bulabildiğim bilgilere göre, {summary}"


def is_low_quality_answer(answer: str) -> bool:
    if not answer or len(answer.strip()) < 20:
        return True

    lowered = answer.lower()
    if FALLBACK_ANSWER.lower() in lowered and answer.strip() != FALLBACK_ANSWER:
        return True

    bad_patterns = [
        'present edilemeyen',
        'informationudur',
        'information present',
        'especialmente',
        'bir yol bulamıyorum',
        'bilgi vermemek',
        'numerous',
        'collaboration',
        'information\'u',
        'mümkün değildir, çünkü',
        'i cannot',
        'i do not have enough information',
        'bağlam bilgisi mevcut değil',
    ]
    return any(pattern in lowered for pattern in bad_patterns)


def _is_address_question(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in (
        'adres', 'address', 'nerede', 'nerde', 'konum', 'lokasyon',
    ))


def _extract_address_from_context(context: str) -> str:
    lines = [line.strip() for line in context.splitlines() if line.strip()]

    def line_score(line: str) -> int:
        lowered = line.lower()
        score = 0
        if 'kampüs' in lowered or 'campus' in lowered:
            score += 4
        if 'caddesi' in lowered or 'cad.' in lowered or 'street' in lowered:
            score += 3
        if re.search(r'\bno[: ]\s*\d+', lowered):
            score += 4
        if '/' in line:
            score += 2
        if any(term in lowered for term in ('istanbul', 'ataşehir', 'kayışdağı')):
            score += 3
        return score

    best_parts: list[str] = []
    best_score = 0
    for idx, line in enumerate(lines):
        score = line_score(line)
        if score <= 0:
            continue

        parts = [line]
        total_score = score

        previous = lines[idx - 1] if idx > 0 else ''
        if previous and line_score(previous) >= 3:
            parts.insert(0, previous)
            total_score += line_score(previous)

        next_line = lines[idx + 1] if idx + 1 < len(lines) else ''
        if next_line and line_score(next_line) >= 2:
            parts.append(next_line)
            total_score += line_score(next_line)

        if total_score > best_score:
            best_score = total_score
            best_parts = list(dict.fromkeys(parts))

    if best_parts:
        return ', '.join(best_parts)
    return ''


def get_answer(question: str, context: str) -> str:
    """
    Send a question with its retrieved context to Ollama and return the answer.
    """
    if not context or not context.strip():
        return FALLBACK_ANSWER

    direct_address = _extract_address_from_context(context)
    if _is_address_question(question) and direct_address:
        return direct_address

    # Reject suspiciously short context — not enough to answer from
    if len(context.strip()) < 80:
        logger.warning("Context too short to be useful, returning fallback")
        return FALLBACK_ANSWER

    user_message = f"""Aşağıdaki BAĞLAM, Acıbadem Üniversitesi veri tabanından alınan ilgili bilgi parçalarını içermektedir.

=== BAĞLAM BAŞLANGICI ===
{context}
=== BAĞLAM SONU ===

Kullanıcının sorusu: {question}

TALİMATLAR:
- Cevabını YALNIZCA yukarıdaki BAĞLAM içindeki bilgilere dayandır.
- Bağlamda bulunmayan hiçbir bilgiyi ekleme veya tahmin etme.
- Bağlamda bu soruya cevap yoksa SADECE şunu yaz:
{FALLBACK_ANSWER}"""

    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 600,
            "top_p": 0.9,
        },
    }

    try:
        response = requests.post(
            f'{settings.OLLAMA_URL}/api/chat',
            json=payload,
            timeout=240,
        )
        response.raise_for_status()
        data = response.json()
        answer = data['message']['content'].strip()
        if is_low_quality_answer(answer):
            logger.warning("Low-quality model answer detected, returning fallback")
            return FALLBACK_ANSWER
        if _is_address_question(question) and direct_address:
            if 'anadolu yakasında' in answer.lower() or 'istanbul' in answer.lower():
                return direct_address
        return answer

    except requests.Timeout:
        logger.error("Ollama request timed out after 120s")
        return (
            "Üzgünüm, yapay zeka servisi şu an yanıt vermiyor. "
            "Lütfen birkaç saniye bekleyip tekrar deneyin."
        )

    except requests.ConnectionError:
        logger.error("Could not connect to Ollama service")
        return (
            "Üzgünüm, yapay zeka servisine bağlanılamıyor. "
            "Servisin çalışıp çalışmadığını kontrol edin."
        )

    except requests.RequestException as e:
        logger.error(f"Ollama request failed: {e}")
        return "Üzgünüm, bir hata oluştu. Lütfen tekrar deneyin."

    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Unexpected Ollama response format: {e}")
        return "Üzgünüm, beklenmeyen bir hata oluştu. Lütfen tekrar deneyin."


def stream_answer(question: str, context: str) -> Iterator[str]:
    """
    Stream the answer from Ollama in small text chunks.
    """
    if not context or not context.strip():
        yield FALLBACK_ANSWER
        return

    direct_address = _extract_address_from_context(context)
    if _is_address_question(question) and direct_address:
        yield direct_address
        return

    if len(context.strip()) < 80:
        logger.warning("Context too short to be useful, streaming fallback")
        yield FALLBACK_ANSWER
        return

    user_message = f"""Aşağıdaki BAĞLAM, Acıbadem Üniversitesi veri tabanından alınan ilgili bilgi parçalarını içermektedir.

=== BAĞLAM BAŞLANGICI ===
{context}
=== BAĞLAM SONU ===

Kullanıcının sorusu: {question}

TALİMATLAR:
- Cevabını YALNIZCA yukarıdaki BAĞLAM içindeki bilgilere dayandır.
- Bağlamda bulunmayan hiçbir bilgiyi ekleme veya tahmin etme.
- Bağlamda bu soruya cevap yoksa SADECE şunu yaz:
{FALLBACK_ANSWER}"""

    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "stream": True,
        "options": {
            "temperature": 0.1,
            "num_predict": 600,
            "top_p": 0.9,
        },
    }

    full_answer: list[str] = []
    try:
        response = requests.post(
            f'{settings.OLLAMA_URL}/api/chat',
            json=payload,
            timeout=240,
            stream=True,
        )
        response.raise_for_status()

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed Ollama stream chunk")
                continue

            piece = (data.get('message') or {}).get('content') or ''
            if not piece:
                continue
            full_answer.append(piece)
            yield piece

        answer = ''.join(full_answer).strip()
        if not answer:
            yield FALLBACK_ANSWER
            return
        if is_low_quality_answer(answer):
            logger.warning("Low-quality streamed model answer detected, streaming fallback")
            yield f"\n{FALLBACK_ANSWER}"
            return
    except requests.Timeout:
        logger.error("Ollama streaming request timed out")
        yield "Üzgünüm, yapay zeka servisi şu an yanıt vermiyor. Lütfen birkaç saniye bekleyip tekrar deneyin."
    except requests.ConnectionError:
        logger.error("Could not connect to Ollama service while streaming")
        yield "Üzgünüm, yapay zeka servisine bağlanılamıyor. Servisin çalışıp çalışmadığını kontrol edin."
    except requests.RequestException as e:
        logger.error(f"Ollama streaming request failed: {e}")
        yield "Üzgünüm, bir hata oluştu. Lütfen tekrar deneyin."
