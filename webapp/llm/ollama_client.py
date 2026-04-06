import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen Acıbadem Üniversitesi'nin resmi yapay zeka asistanısın.
Görevin, öğrencilere ve ziyaretçilere üniversite hakkında doğru, güvenilir ve yardımcı bilgiler vermektir.

TEMEL KURALLAR:
1. Yanıtlarını YALNIZCA sana verilen bağlam (context) bilgisine dayandır.
2. Bağlamda bulunmayan bilgileri kesinlikle uydurma veya tahmin etme.
3. Bağlamda bilgi yoksa şunu söyle: "Bu konuda elimde yeterli bilgi bulunmuyor. Daha fazla bilgi için https://www.acibadem.edu.tr adresini ziyaret edebilir veya üniversiteyle iletişime geçebilirsiniz."
4. Kullanıcı Türkçe yazıyorsa Türkçe, İngilizce yazıyorsa İngilizce yanıt ver.
5. Yanıtların net, doğru ve anlaşılır olsun.
6. Akademik programlar, ders içerikleri, ücretler, kabul koşulları, kampüs, iletişim bilgileri gibi konularda bağlamdaki bilgileri kullan.
7. Resmi ve yardımcı bir dil kullan.
8. Türkçe yanıt verirken İngilizce-Türkçe karışık, anlamsız veya bozuk cümleler kurma.
9. Soruya uygun yeterli bilgi varsa 2-5 cümlelik kısa bir özet ver; yeterli bilgi yoksa 3. kuraldaki metni aynen kullan."""

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
    Send a question with its retrieved context to Ollama (llama3.2:3b)
    and return the generated answer.
    """
    if not context or not context.strip():
        return FALLBACK_ANSWER

    user_message = f"""Aşağıda Acıbadem Üniversitesi web sitesinden alınan güncel bilgiler bulunmaktadır:

--- BAĞLAM BAŞLANGICI ---
{context}
--- BAĞLAM SONU ---

Kullanıcının sorusu: {question}

Kurallar:
- Yalnızca yukarıdaki bağlamı kullan.
- Soruyla ilgili net bilgi varsa kısa ve düzgün bir özet ver.
- Soruyla ilgili yeterli bilgi yoksa şu cümleyi aynen yaz:
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
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        answer = data['message']['content'].strip()
        if is_low_quality_answer(answer):
            logger.warning("Low-quality model answer detected, using context fallback")
            return build_context_fallback(context)
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
