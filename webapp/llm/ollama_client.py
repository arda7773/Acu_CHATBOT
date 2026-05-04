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
9. Kullanıcı adresi, konumu veya "nerede/nerde" bilgisini soruyorsa ve bağlamda açık adres varsa, genel konum özeti verme; açık adresi aynen ve doğrudan yaz.
10. Kullanıcı ulaşım, servis, metro, otobüs veya kampüse nasıl gidileceğini soruyorsa; bağlamda geçen ulaşım türlerini, servis saatini, güzergah/adım bilgisini ve açık adresi TEK yanıtta eksiksiz ver. Kısa özet yapıp madde atlama."""

FALLBACK_ANSWER = (
    "Bu konuda elimde yeterli bilgi bulunmuyor. Daha fazla bilgi için "
    "https://www.acibadem.edu.tr adresini ziyaret edebilir veya üniversiteyle iletişime geçebilirsiniz."
)

_FALLBACK_FIRST_SENTENCE = "Bu konuda elimde yeterli bilgi bulunmuyor."

_COURSE_DETAIL_TERMS = (
    'ders içeriği', 'ders icerigi', 'dersinin içeriği', 'dersinin icerigi',
    'dersin içeriği', 'dersin icerigi', 'ders hakkında', 'ders hakkinda',
    'course content', 'course details',
)

_ACADEMIC_TITLES = (
    'Prof. Dr.', 'Doç. Dr.', 'Dr. Öğr. Üyesi', 'Öğr. Gör. Dr.',
    'Arş. Gör.', 'Uzman Dr.', 'Öğr. Gör.',
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


def _strip_fallback_from_answer(answer: str) -> str:
    """Remove fallback text the LLM appended to an otherwise valid answer."""
    stripped = answer.strip()
    stripped_lower = stripped.lower()
    fa_lower = FALLBACK_ANSWER.lower()
    fs_lower = _FALLBACK_FIRST_SENTENCE.lower()

    if stripped_lower in (fa_lower, fs_lower):
        return stripped

    for phrase_lower in (fa_lower, fs_lower):
        idx = stripped_lower.rfind(phrase_lower)
        if idx > 30:
            cleaned = stripped[:idx].strip()
            if len(cleaned) >= 20:
                return cleaned

    return stripped


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


def _normalise_for_match(text: str) -> str:
    replacements = str.maketrans({
        'ç': 'c', 'ğ': 'g', 'ı': 'i', 'İ': 'i', 'ö': 'o', 'ş': 's', 'ü': 'u',
    })
    return text.lower().translate(replacements)


def _is_course_detail_question(question: str) -> bool:
    lowered = _normalise_for_match(question)
    return any(_normalise_for_match(term) in lowered for term in _COURSE_DETAIL_TERMS)


def _is_staff_list_question(question: str) -> bool:
    lowered = _normalise_for_match(question)
    return any(term in lowered for term in (
        'akademik kadro', 'akadem kadro', 'akadem kadrosu', 'akadem kadrosunu',
        'kadrosunu say', 'hocalar', 'hoca', 'ogretim uyesi',
        'ogretim uyeleri', 'kimler var', 'faculty members',
    ))


def _is_bologna_process_question(question: str) -> bool:
    lowered = _normalise_for_match(question)
    return 'bologna' in lowered and ('surec' in lowered or 'nedir' in lowered)


def _next_value(lines: list[str], label: str) -> str:
    folded_label = _normalise_for_match(label)
    for index, line in enumerate(lines):
        if _normalise_for_match(line) != folded_label:
            continue
        for candidate in lines[index + 1:]:
            if candidate and _normalise_for_match(candidate) != folded_label:
                return candidate
    return ''


def _section_value(lines: list[str], label: str, stop_labels: tuple[str, ...]) -> str:
    folded_label = _normalise_for_match(label)
    folded_stops = {_normalise_for_match(stop) for stop in stop_labels}
    for index, line in enumerate(lines):
        if _normalise_for_match(line) != folded_label:
            continue

        values = []
        for candidate in lines[index + 1:]:
            folded = _normalise_for_match(candidate)
            if folded in folded_stops:
                break
            if candidate:
                values.append(candidate)
        return ' '.join(values).strip()
    return ''


def _direct_course_detail_answer(question: str, context: str) -> str:
    """
    Bologna course-detail pages have a stable label/value structure. For these
    questions, extracting the answer is more reliable than asking the LLM to
    infer whether the context is sufficient.
    """
    if not _is_course_detail_question(question):
        return ''
    if 'DERS BİLGİ PAKETİ' not in context and 'Dersin İçeriği' not in context:
        return ''

    lines = [line.strip() for line in context.splitlines() if line.strip()]
    header = next((line for line in lines if 'DERS BİLGİ PAKETİ:' in line), '')
    code = ''
    name = ''
    header_match = re.search(r'DERS BİLGİ PAKETİ:\s*([A-Z]{2,4}\s*\d{3,4})\s*-\s*(.*?)\s*---', header)
    if header_match:
        code = re.sub(r'\s+', ' ', header_match.group(1)).strip()
        name = re.sub(r'\s+', ' ', header_match.group(2)).strip()

    if not code:
        code = _next_value(lines, 'Kodu')
    if not name:
        name = _next_value(lines, 'Adı')

    stop_labels = (
        'Dersin Yöntem ve Teknikleri', 'Ön Koşulları', 'Dersin Koordinatörü',
        'Dersi Verenler', 'Dersin Yardımcıları', 'Dersin Staj Durumu',
        'Ders Kaynakları', 'Ders Yapısı', 'Planlanan Öğrenme Aktiviteleri ve Metodları',
        'Değerlendirme Ölçütleri', 'AKTS Hesaplama İçeriği',
        'Dersin Öğrenme Çıktıları', 'Ders Konuları',
    )
    content = _section_value(lines, 'Dersin İçeriği', stop_labels)
    purpose = _section_value(lines, 'Dersin Amacı', ('Dersin İçeriği',) + stop_labels)
    level = _next_value(lines, 'Dersin Düzeyi')
    program = _next_value(lines, 'Bölümü / Programı')
    language = _next_value(lines, 'Dersin Dili')
    course_type = _next_value(lines, 'Dersin Türü')
    teaching_mode = _next_value(lines, 'Dersin Öğretim Şekli')
    coordinator = _next_value(lines, 'Dersin Koordinatörü')

    if not content and not purpose:
        return ''

    title = ' - '.join(part for part in (code, name) if part)
    answer_lines = []
    if title:
        answer_lines.append(f"{title} dersi için bağlamdaki bilgiler şöyle:")
    else:
        answer_lines.append("Bağlamdaki ders bilgileri şöyle:")

    if program:
        answer_lines.append(f"Program: {program}")
    if level:
        answer_lines.append(f"Düzey: {level}")
    if language:
        answer_lines.append(f"Ders dili: {language}")
    if course_type:
        answer_lines.append(f"Ders türü: {course_type}")
    if teaching_mode:
        answer_lines.append(f"Öğretim şekli: {teaching_mode}")
    if purpose:
        answer_lines.append(f"Dersin amacı: {purpose}")
    if content:
        answer_lines.append(f"Dersin içeriği: {content}")
    if coordinator:
        answer_lines.append(f"Dersin koordinatörü: {coordinator}")

    return '\n'.join(answer_lines)


def _looks_like_person_name(text: str) -> bool:
    if not text or len(text) < 5:
        return False
    if any(char.isdigit() for char in text):
        return False
    folded = _normalise_for_match(text)
    blocked = {
        'akademik kadro', 'akademisyen adi', 'tanitim katalogu', 'sanal tur',
        'sor', 'cevaplayalim', 'temel tip bilimleri', 'dahili tip bilimleri',
        'cerrahi tip bilimleri',
    }
    if folded in blocked:
        return False
    words = text.replace('-', ' ').split()
    return len(words) >= 2 and any(char.isupper() for char in text)


_TITLE_RANK: dict[str, int] = {t: i for i, t in enumerate(_ACADEMIC_TITLES)}


def _direct_staff_answer(question: str, context: str) -> str:
    if not _is_staff_list_question(question):
        return ''
    if 'Akademik Kadro' not in context and 'Akademisyen Adı' not in context:
        return ''

    lines = [line.strip() for line in context.splitlines() if line.strip()]
    staff: list[str] = []
    name_to_idx: dict[str, int] = {}
    pending_head = False
    title = ''
    title_set = set(_ACADEMIC_TITLES)
    footnote_mode = False

    for line in lines:
        if line.startswith('==='):
            pending_head = False
            title = ''
            footnote_mode = False
            continue

        if line == 'Anabilim Dalı Başkanı':
            pending_head = True
            continue

        if line in title_set:
            title = line
            footnote_mode = False
            continue

        if re.fullmatch(r'\({1,3}\*{1,3}\)', line):
            footnote_mode = True
            continue
        if footnote_mode and re.search(r'Kanun|Madde|Uyarınca|Görevlendirilen', line, re.IGNORECASE):
            continue

        if title and _looks_like_person_name(line):
            prefix = 'Anabilim Dalı Başkanı ' if pending_head else ''
            item = f"{prefix}{title} {line}"
            name_key = _normalise_for_match(line)

            if name_key in name_to_idx:
                existing_idx = name_to_idx[name_key]
                existing = staff[existing_idx]
                existing_has_head = existing.startswith('Anabilim Dalı Başkanı')
                has_head = bool(prefix)
                if has_head and not existing_has_head:
                    staff[existing_idx] = item
                elif not has_head and not existing_has_head:
                    existing_title = next((t for t in _ACADEMIC_TITLES if t in existing), None)
                    if _TITLE_RANK.get(title, 99) < _TITLE_RANK.get(existing_title, 99):
                        staff[existing_idx] = item
            else:
                name_to_idx[name_key] = len(staff)
                staff.append(item)
            pending_head = False

    if not staff:
        return ''

    answer_lines = [
        f"Elimdeki akademik kadro kaydına göre listede {len(staff)} kişi görünüyor:",
        '',
    ]
    answer_lines.extend(f"{index}. {name}" for index, name in enumerate(staff, 1))
    return '\n'.join(answer_lines)


def _direct_bologna_process_answer(question: str, context: str) -> str:
    if not _is_bologna_process_question(question):
        return ''
    if 'Bologna Süreci Nedir?' not in context:
        return ''

    marker = 'Bologna Süreci Nedir?'
    section = context.split(marker, 1)[1]
    section = section.split('\n===', 1)[0]
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    if not lines:
        return ''

    paragraphs = []
    current = []
    for line in lines:
        current.append(line)
        if line.endswith(('.', ':')):
            paragraphs.append(' '.join(current))
            current = []
    if current:
        paragraphs.append(' '.join(current))

    answer = '\n\n'.join(paragraphs[:4]).strip()
    if not answer:
        return ''
    return f"Bologna Süreci hakkında kaynakta yer alan bilgi şöyle:\n\n{answer}"


def _direct_catalog_listing_answer(context: str) -> str:
    markers = (
        '=== DOĞRUDAN KATALOG YANITI ===',
        '=== DOĞRUDAN DERS PLANI YANITI ===',
        '=== DOĞRUDAN DERS KODU YANITI ===',
        '=== DOĞRUDAN KİŞİ YANITI ===',
    )
    marker = next((candidate for candidate in markers if candidate in context), '')
    if not marker:
        return ''
    section = context.split(marker, 1)[1]
    if 'YANIT:' in section:
        section = section.split('YANIT:', 1)[1]
    answer = section.strip()
    return answer if answer else ''


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
        if line.startswith('==='):
            continue
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

    direct_catalog_answer = _direct_catalog_listing_answer(context)
    if direct_catalog_answer:
        return direct_catalog_answer

    direct_address = _extract_address_from_context(context)
    if _is_address_question(question) and direct_address:
        return direct_address

    # Reject suspiciously short context — not enough to answer from
    if len(context.strip()) < 80:
        logger.warning("Context too short to be useful, returning fallback")
        return FALLBACK_ANSWER

    direct_course_answer = _direct_course_detail_answer(question, context)
    if direct_course_answer:
        return direct_course_answer

    direct_staff_answer = _direct_staff_answer(question, context)
    if direct_staff_answer:
        return direct_staff_answer

    direct_bologna_answer = _direct_bologna_process_answer(question, context)
    if direct_bologna_answer:
        return direct_bologna_answer

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
        answer = _strip_fallback_from_answer(data['message']['content'].strip())
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

    direct_catalog_answer = _direct_catalog_listing_answer(context)
    if direct_catalog_answer:
        yield direct_catalog_answer
        return

    direct_address = _extract_address_from_context(context)
    if _is_address_question(question) and direct_address:
        yield direct_address
        return

    if len(context.strip()) < 80:
        logger.warning("Context too short to be useful, streaming fallback")
        yield FALLBACK_ANSWER
        return

    direct_course_answer = _direct_course_detail_answer(question, context)
    if direct_course_answer:
        yield direct_course_answer
        return

    direct_staff_answer = _direct_staff_answer(question, context)
    if direct_staff_answer:
        yield direct_staff_answer
        return

    direct_bologna_answer = _direct_bologna_process_answer(question, context)
    if direct_bologna_answer:
        yield direct_bologna_answer
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

        answer = _strip_fallback_from_answer(''.join(full_answer).strip())
        if not answer:
            yield FALLBACK_ANSWER
            return
        if is_low_quality_answer(answer):
            logger.warning("Low-quality streamed model answer detected, streaming fallback")
            yield FALLBACK_ANSWER
            return
        yield answer
    except requests.Timeout:
        logger.error("Ollama streaming request timed out")
        yield "Üzgünüm, yapay zeka servisi şu an yanıt vermiyor. Lütfen birkaç saniye bekleyip tekrar deneyin."
    except requests.ConnectionError:
        logger.error("Could not connect to Ollama service while streaming")
        yield "Üzgünüm, yapay zeka servisine bağlanılamıyor. Servisin çalışıp çalışmadığını kontrol edin."
    except requests.RequestException as e:
        logger.error(f"Ollama streaming request failed: {e}")
        yield "Üzgünüm, bir hata oluştu. Lütfen tekrar deneyin."
