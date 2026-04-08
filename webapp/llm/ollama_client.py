import json
import logging

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
8. Sayılar, tarihler, isimler: YALNIZCA bağlamda geçen değerleri kullan, asla tahmin etme."""

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


def get_answer(question: str, context: str) -> str:
    """
    Send a question with its retrieved context to Ollama and return the answer.
    """
    if not context or not context.strip():
        return FALLBACK_ANSWER

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
