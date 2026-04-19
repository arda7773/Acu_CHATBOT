import re
import time
import logging
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from statistics import mean
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from django.db.models import Q
from django.utils import timezone

from scraper.content_metadata import classify_page_type, infer_department, infer_faculty

logger = logging.getLogger(__name__)

# Minimum cosine similarity distance threshold for semantic results.
# pgvector CosineDistance returns 0 (identical) to 2 (opposite).
# Results with distance > this value are considered irrelevant.
SEMANTIC_DISTANCE_THRESHOLD = 0.6

HEADERS = {
    'User-Agent': 'ACU-ChatBot/1.0 (Educational Project - CSE322)',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
}

# Common Turkish and English stop words to filter out
STOP_WORDS = {
    # Turkish
    've', 'veya', 'ile', 'bir', 'bu', 'şu', 'da', 'de', 'ki', 'için',
    'gibi', 'kadar', 'ama', 'fakat', 'ancak', 'çünkü', 'eğer', 'ise',
    'ne', 'nasıl', 'neden', 'nerede', 'hangi', 'hakkında', 'var', 'yok',
    'mı', 'mi', 'mu', 'mü', 'olan', 'olarak', 'daha',
    'çok', 'en', 'her', 'hiç', 'bazı', 'tüm', 'bütün', 'bana', 'benim',
    'bilgi', 'ver', 'verir', 'verebilir', 'bilir', 'lütfen',
    'bölüm', 'bölümü', 'program', 'programı',
    'üniversiteye', 'üniversiteden', 'üniversitesinde',
    'üniversitenin', 'üniversitesinin',
    'söyle', 'söyler', 'söyleyebilir', 'anlat', 'anlatır', 'listele',
    'hepsi', 'hepsini', 'hepsinde', 'tümünü', 'tamamını',
    'neler', 'nelerdir', 'kaçtane', 'kaçtanedir',
    'acıbadem', 'acibadem',
    # English
    'the', 'is', 'are', 'was', 'were', 'what', 'how', 'where', 'when',
    'who', 'which', 'about', 'from', 'that', 'this', 'have', 'has',
    'not', 'can', 'will', 'would', 'could', 'should', 'tell', 'give',
    'information', 'please', 'and', 'or', 'but', 'with', 'for', 'its',
    'does', 'do', 'list', 'all',
}

# Turkish suffixes ordered longest-first for greedy stripping
_TR_SUFFIXES = [
    'lerin', 'ların', 'lerin', 'nüzün', 'nizin', 'nızın', 'nunun',
    'lere', 'lara', 'leri', 'ları', 'lerde', 'larda', 'lerden', 'lardan',
    'ler', 'lar',
    'nden', 'ndan', 'nde', 'nda', 'nün', 'nun', 'nin', 'nın',
    'den', 'dan', 'ten', 'tan', 'de', 'da', 'te', 'ta',
    'nün', 'nun', 'nin', 'nın',
    'ün', 'un', 'in', 'ın',
    'ye', 'ya', 'e', 'a',
    'yi', 'yı', 'yü', 'yu', 'i', 'ı', 'ü', 'u',
    'dir', 'dır', 'dür', 'dur', 'tir', 'tır', 'tür', 'tur',
]

ADMISSION_TERMS = {
    'başvuru', 'basvuru', 'kabul', 'burs', 'kontenjan', 'taban puan',
    'application', 'admission', 'requirements', 'tuition',
}

CAMPUS_TERMS = {
    'kampüs', 'kampus', 'yerleşke', 'yerleske', 'kütüphane', 'kutuphane',
    'ulaşım', 'ulasim', 'yemekhane', 'kafeterya', 'spor merkezi',
}

CONTACT_TERMS = {
    'iletişim', 'iletisim', 'telefon', 'e-posta', 'eposta', 'email',
    'adres', 'address', 'contact',
}

HEAD_KEYWORDS = {
    'bölüm başkanı', 'bolum baskani', 'anabilim dalı başkanı', 'program başkanı',
    'program baskani', 'chair', 'head of department', 'director',
}

DEPARTMENT_ALIASES = {
    'Bilgisayar Mühendisliği': (
        'bilgisayar mühendisliği', 'bilgisayar muhendisligi',
        'computer engineering', 'computer engineer',
    ),
    'Bilgisayar Programcılığı': (
        'bilgisayar programcılığı', 'bilgisayar programciligi',
        'computer programming',
    ),
    'Biyomedikal Mühendisliği': (
        'biyomedikal mühendisliği', 'biyomedikal muhendisligi',
        'biomedical engineering',
    ),
    'Moleküler Biyoloji ve Genetik': (
        'moleküler biyoloji ve genetik', 'molekuler biyoloji ve genetik',
        'molecular biology and genetics',
    ),
    'Psikoloji': ('psikoloji', 'psychology'),
    'Hemşirelik': ('hemşirelik', 'hemsirelik', 'nursing'),
    'Beslenme ve Diyetetik': (
        'beslenme ve diyetetik', 'nutrition and dietetics',
    ),
    'Eczacılık': ('eczacılık', 'eczacilik', 'pharmacy'),
}

DEPARTMENT_URL_SLUGS = {
    'Bilgisayar Mühendisliği': ('bilgisayar-muhendisligi', 'computer-engineering'),
    'Bilgisayar Programcılığı': ('bilgisayar-programciligi', 'computer-programming'),
    'Biyomedikal Mühendisliği': ('biyomedikal-muhendisligi', 'biomedical-engineering'),
    'Moleküler Biyoloji ve Genetik': (
        'molekuler-biyoloji-ve-genetik',
        'molecular-biology-and-genetics',
    ),
    'Psikoloji': ('psikoloji', 'psychology'),
    'Hemşirelik': ('hemsirelik', 'nursing'),
    'Beslenme ve Diyetetik': ('beslenme-ve-diyetetik', 'nutrition-and-dietetics'),
    'Eczacılık': ('eczacilik', 'pharmacy'),
}

FACULTY_ALIASES = {
    'Mühendislik ve Doğa Bilimleri Fakültesi': (
        'mühendislik ve doğa bilimleri fakültesi',
        'muhendislik ve doga bilimleri fakultesi',
        'faculty of engineering and natural sciences',
    ),
    'Meslek Yüksekokulu': (
        'meslek yüksekokulu', 'vocational school', 'yüksekokul'
    ),
}

FACULTY_URL_SLUGS = {
    'Mühendislik ve Doğa Bilimleri Fakültesi': (
        'muhendislik-ve-doga-bilimleri-fakultesi',
        'faculty-of-engineering-and-natural-sciences',
    ),
}


def normalize_text(text: str) -> str:
    text = unicodedata.normalize('NFKC', (text or '')).lower()
    return re.sub(r'\s+', ' ', text).strip()


def _ascii_fold(text: str) -> str:
    replacements = str.maketrans({
        'ç': 'c', 'ğ': 'g', 'ı': 'i', 'İ': 'i', 'ö': 'o', 'ş': 's', 'ü': 'u',
    })
    return normalize_text(text).translate(replacements)


def _normalize_compact(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', _ascii_fold(text))


def _word_similarity(left: str, right: str) -> float:
    left_compact = _normalize_compact(stem_turkish(_ascii_fold(left)))
    right_compact = _normalize_compact(stem_turkish(_ascii_fold(right)))
    if not left_compact or not right_compact:
        return 0.0
    if left_compact == right_compact:
        return 1.0
    if len(left_compact) >= 5 and len(right_compact) >= 5:
        if left_compact.startswith(right_compact) or right_compact.startswith(left_compact):
            return 0.96
    return SequenceMatcher(None, left_compact, right_compact).ratio()


def _contains_fuzzy_phrase(text: str, phrase: str, threshold: float = 0.86) -> bool:
    normalized_text = _ascii_fold(text)
    normalized_phrase = _ascii_fold(phrase)
    if not normalized_text or not normalized_phrase:
        return False
    if normalized_phrase in normalized_text:
        return True

    phrase_words = normalized_phrase.split()
    text_words = normalized_text.split()
    if not phrase_words or not text_words:
        return False

    min_window = max(1, len(phrase_words) - 1)
    max_window = min(len(text_words), len(phrase_words) + 1)
    for size in range(min_window, max_window + 1):
        for idx in range(len(text_words) - size + 1):
            candidate = ' '.join(text_words[idx:idx + size])
            if SequenceMatcher(None, _normalize_compact(candidate), _normalize_compact(normalized_phrase)).ratio() >= threshold:
                return True

    if len(phrase_words) == 1:
        return any(_word_similarity(word, phrase_words[0]) >= threshold for word in text_words)

    return False


def stem_turkish(word: str) -> str:
    """Strip common Turkish suffixes to get an approximate stem."""
    if len(word) <= 4:
        return word
    for suffix in _TR_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def is_listing_question(text: str) -> bool:
    """Detect questions that ask for a complete list (e.g. 'hangi fakülteler var')."""
    words = set(normalize_text(text).split())
    listing_phrases = {
        'hangi', 'hepsi', 'hepsini', 'tüm', 'bütün', 'listele',
        'neler', 'nelerdir', 'say', 'tamamı', 'tamamını',
    }
    return bool(words & listing_phrases)


def is_head_question(text: str) -> bool:
    return any(_contains_fuzzy_phrase(text, keyword, threshold=0.84) for keyword in HEAD_KEYWORDS)


def extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from a question, including Turkish stems."""
    text = normalize_text(text)
    text = re.sub(r'[^\w\s]', ' ', text)
    words = text.split()
    keywords = []
    seen = set()
    for w in words:
        if w in STOP_WORDS or len(w) <= 2:
            continue
        if w not in seen:
            keywords.append(w)
            seen.add(w)
        # Also add the stemmed form if different and not a stop word
        stem = stem_turkish(w)
        if stem != w and stem not in STOP_WORDS and len(stem) > 2 and stem not in seen:
            keywords.append(stem)
            seen.add(stem)

    for department in _find_department_mentions(text):
        canonical_name = _department_display_name(department)
        canonical_tokens = normalize_text(canonical_name).split()
        for token in canonical_tokens:
            if token not in STOP_WORDS and token not in seen:
                keywords.append(token)
                seen.add(token)
            stem = stem_turkish(token)
            if stem != token and stem not in STOP_WORDS and len(stem) > 2 and stem not in seen:
                keywords.append(stem)
                seen.add(stem)
    return keywords


def extract_course_codes(text: str) -> list[str]:
    return [
        re.sub(r'[\s-]+', '', match.upper())
        for match in re.findall(r'\b[A-Za-z]{2,4}\s?-?\s?\d{3,4}\b', text or '')
    ]


def _find_department_mentions(text: str) -> list[str]:
    hits = []
    for label, aliases in DEPARTMENT_ALIASES.items():
        if any(_contains_fuzzy_phrase(text, alias, threshold=0.84) for alias in aliases):
            hits.append(label.lower())
    return hits


def _primary_department(question: str) -> str:
    matches = _find_department_mentions(question)
    return matches[0] if matches else ''


def _primary_faculty(question: str) -> str:
    normalized = normalize_text(question)
    for label, aliases in FACULTY_ALIASES.items():
        if any(_contains_fuzzy_phrase(normalized, alias, threshold=0.84) for alias in aliases):
            return label.lower()
    return ''


def _department_slug_variants(department: str) -> tuple[str, ...]:
    canonical = next((name for name in DEPARTMENT_URL_SLUGS if name.lower() == department), '')
    return DEPARTMENT_URL_SLUGS.get(canonical, ())


def _faculty_slug_variants(faculty: str) -> tuple[str, ...]:
    canonical = next((name for name in FACULTY_URL_SLUGS if name.lower() == faculty), '')
    return FACULTY_URL_SLUGS.get(canonical, ())


def _department_display_name(department: str) -> str:
    return next((name for name in DEPARTMENT_ALIASES if name.lower() == department), department)


def _faculty_display_name(faculty: str) -> str:
    return next((name for name in FACULTY_ALIASES if name.lower() == faculty), faculty)


def _has_department_match(haystack: str, department: str) -> bool:
    if not department:
        return False
    canonical = next((name for name in DEPARTMENT_ALIASES if name.lower() == department), '')
    aliases = DEPARTMENT_ALIASES.get(canonical, ())
    slugs = DEPARTMENT_URL_SLUGS.get(canonical, ())
    lowered = normalize_text(haystack)
    return (
        any(_contains_fuzzy_phrase(haystack, alias, threshold=0.84) for alias in aliases) or
        any(slug in lowered for slug in slugs)
    )


def _mentions_other_department(haystack: str, department: str) -> bool:
    lowered = normalize_text(haystack)
    for name, aliases in DEPARTMENT_ALIASES.items():
        if name.lower() == department:
            continue
        slugs = DEPARTMENT_URL_SLUGS.get(name, ())
        if any(_contains_fuzzy_phrase(haystack, alias, threshold=0.84) for alias in aliases) or any(slug in lowered for slug in slugs):
            return True
    return False


def _format_course_code(code: str) -> str:
    code = re.sub(r'[\s-]+', '', (code or '').upper())
    match = re.match(r'^([A-Z]{2,4})(\d{3,4})$', code)
    if not match:
        return code
    return f"{match.group(1)} {match.group(2)}"


def extract_named_entities(question: str) -> dict[str, list[str]]:
    normalized = normalize_text(question)
    faculty_hits = []
    dept_hits = _find_department_mentions(question)

    for label in (
        'tıp fakültesi', 'eczacılık fakültesi', 'sağlık bilimleri fakültesi',
        'mühendislik ve doğa bilimleri fakültesi', 'fen edebiyat fakültesi',
        'meslek yüksekokulu', 'lisansüstü eğitim enstitüsü',
    ):
        if label in normalized:
            faculty_hits.append(label)

    return {
        'course_codes': extract_course_codes(question),
        'faculties': faculty_hits,
        'departments': list(dict.fromkeys(dept_hits)),
    }


# ===========================================================================
# INTENT DETECTION
# ===========================================================================

INTENT_GENERAL      = 'general'
INTENT_BOLOGNA      = 'bologna'
INTENT_STAFF        = 'staff'
INTENT_CAMPUS       = 'campus'
INTENT_STUDENT_LIFE = 'student_life'
INTENT_COURSE       = 'course'
INTENT_DEPARTMENT   = 'department'
INTENT_ADMISSION    = 'admission'
INTENT_CONTACT      = 'contact'
INTENT_ANNOUNCEMENT = 'announcement'

# Signals per intent — checked in priority order (more specific first)
_INTENT_SIGNALS: dict[str, list[str]] = {
    INTENT_BOLOGNA: [
        'bologna', 'obs.acibadem', 'akts', 'ects', 'ders planı', 'müfredat',
        'program çıktıları', 'öğrenme çıktıları',
    ],
    INTENT_STAFF: [
        'akademik kadro', 'öğretim üyesi', 'hocalar', 'hoca', 'ogretim uyesi',
        'profesör', 'doçent', 'araştırma görevlisi', 'faculty members', 'kimler var',
    ],
    INTENT_ADMISSION: [
        'başvuru', 'kayıt', 'kabul', 'yks', 'tyt', 'ayt', 'puan',
        'kontenjan', 'taban puan', 'yerleşme', 'nasıl girilir',
        'admission', 'application', 'requirements', 'burs', 'ücret', 'harç',
    ],
    INTENT_CONTACT: [
        'iletişim', 'adres', 'telefon', 'e-posta', 'eposta',
        'contact', 'phone', 'email', 'address', 'nerede', 'lokasyon',
    ],
    INTENT_CAMPUS: [
        'kampüs', 'kampus', 'yerleşke', 'bina', 'tesis', 'tesisler',
        'kütüphane', 'kutuphane', 'laboratuvar', 'lab ', 'kafeterya',
        'kantin', 'yemekhane', 'ulaşım', 'ulasim', 'metro', 'otobüs',
        'servis', 'spor salonu', 'spor merkezi', 'sosyal alan', 'öğrenci merkezi',
    ],
    INTENT_STUDENT_LIFE: [
        'öğrenci yaşamı', 'ogrenci yasami', 'kulüp', 'kulüpler', 'aktivite',
        'sosyal', 'barınma', 'yurt', 'konaklama', 'öğrenci hayatı',
    ],
    INTENT_COURSE: [
        'ders', 'dersler', 'kredi', 'ders kodu', 'ders içeriği',
        'haftalık ders', 'zorunlu ders', 'seçmeli ders', 'course', 'curriculum',
    ],
    INTENT_ANNOUNCEMENT: [
        'haber', 'haberler', 'duyuru', 'duyurular', 'son dakika',
        'announcement', 'news', 'etkinlik', 'etkinlikler',
    ],
    INTENT_DEPARTMENT: [
        'fakülte', 'bölüm', 'department', 'faculty', 'lisans', 'yüksek lisans',
        'doktora', 'önlisans', 'mühendislik', 'tıp', 'hukuk',
        'hemşirelik', 'psikoloji', 'diş', 'eczacılık',
    ],
}

_INTENT_PRIORITY = [
    INTENT_BOLOGNA, INTENT_STAFF, INTENT_ADMISSION, INTENT_CONTACT,
    INTENT_CAMPUS, INTENT_STUDENT_LIFE, INTENT_COURSE,
    INTENT_ANNOUNCEMENT, INTENT_DEPARTMENT,
]


def detect_intent(text: str) -> str:
    """Return the primary intent of the question."""
    for intent in _INTENT_PRIORITY:
        if any(_contains_fuzzy_phrase(text, signal, threshold=0.83) for signal in _INTENT_SIGNALS[intent]):
            return intent
    return INTENT_GENERAL


# Backwards-compat helpers used in keyword fallback path
def is_staff_question(text: str) -> bool:
    return detect_intent(text) == INTENT_STAFF

def is_bologna_question(text: str) -> bool:
    return detect_intent(text) == INTENT_BOLOGNA

def is_acu_site_question(text: str) -> bool:
    return detect_intent(text) in (
        INTENT_ADMISSION, INTENT_CONTACT, INTENT_ANNOUNCEMENT,
        INTENT_CAMPUS, INTENT_STUDENT_LIFE,
    )

def is_campus_question(text: str) -> bool:
    return detect_intent(text) == INTENT_CAMPUS


# ===========================================================================
# URL STABILITY CLASSIFIER
# Scores URLs as stable/factual (+) or noisy/promotional (-)
# ===========================================================================

_URL_STABILITY_RULES: list[tuple[str, float, str]] = [
    # Highly stable factual pages — positive scores
    ('/akademik-kadro',            +0.25, 'staff'),
    ('/iletisim',                  +0.20, 'contact'),
    ('/kampus',                    +0.18, 'campus'),
    ('/ogrenci-yasami',            +0.15, 'student_life'),
    ('/hakkinda',                  +0.15, 'about'),
    ('/bolum-baskaninin-mesaji',   +0.12, 'about'),
    ('/bolumler/',                 +0.12, 'department'),
    ('/akademik/',                 +0.10, 'academic'),
    ('/lisans/',                   +0.08, 'undergraduate'),
    ('/yuksek-lisans/',            +0.08, 'graduate'),
    # Noisy / promotional / ephemeral — negative scores
    ('/duyurular/',                -0.35, 'announcement'),
    ('/haberler/',                 -0.35, 'news'),
    ('/etkinlikler/',              -0.35, 'event'),
    ('/basin/',                    -0.25, 'press'),
    ('/sanal-tur',                 -0.30, 'promo'),
    ('/tanitim',                   -0.20, 'promo'),
    ('/galeri',                    -0.20, 'gallery'),
    ('/slider',                    -0.30, 'promo'),
    ('/anasayfa',                  -0.20, 'homepage'),
]

# Preferred page types per intent (intent_boost applied when matched)
_INTENT_PREFERRED_PAGE_TYPES: dict[str, list[str]] = {
    INTENT_STAFF:        ['staff'],
    INTENT_CAMPUS:       ['campus', 'student_life', 'about'],
    INTENT_STUDENT_LIFE: ['student_life', 'campus'],
    INTENT_CONTACT:      ['contact'],
    INTENT_ADMISSION:    ['academic', 'undergraduate', 'graduate', 'about'],
    INTENT_DEPARTMENT:   ['department', 'academic', 'undergraduate', 'graduate'],
    INTENT_COURSE:       ['department', 'academic'],
    INTENT_BOLOGNA:      ['academic', 'department'],
    INTENT_ANNOUNCEMENT: ['announcement', 'news', 'event'],
    INTENT_GENERAL:      [],
}

# Page types that should be penalised unless the user is asking about them
_NOISY_PAGE_TYPES = {'announcement', 'news', 'event', 'press', 'promo', 'gallery', 'homepage'}


def classify_url(url: str) -> tuple[float, str]:
    """Return (stability_score, page_type) for a URL based on path patterns."""
    url_lower = url.lower()
    for pattern, score, page_type in _URL_STABILITY_RULES:
        if pattern in url_lower:
            return score, page_type
    return 0.0, 'unknown'


# ===========================================================================
# QUERY EXPANSION
# ===========================================================================

_QUERY_EXPANSIONS: dict[str, list[str]] = {
    INTENT_BOLOGNA: [
        'ders planı', 'müfredat', 'akts kredisi', 'program çıktıları',
        'öğrenme çıktıları', 'yarıyıl', 'bologna süreci',
    ],
    INTENT_STAFF: [
        'öğretim üyesi', 'akademik kadro', 'doçent', 'profesör',
        'araştırma görevlisi', 'dr öğr üyesi',
    ],
    INTENT_CAMPUS: [
        'kampüs', 'yerleşke', 'tesis', 'bina', 'kütüphane',
        'ulaşım', 'spor', 'kafeterya', 'öğrenci hayatı', 'laboratuvar',
    ],
    INTENT_STUDENT_LIFE: [
        'öğrenci kulüpleri', 'sosyal aktiviteler', 'barınma', 'yurt',
        'konaklama', 'öğrenci yaşamı', 'kampüs hayatı',
    ],
    INTENT_COURSE: [
        'ders listesi', 'müfredat', 'zorunlu dersler', 'seçmeli dersler',
        'kredi saati', 'ders planı', 'haftalık program',
    ],
    INTENT_DEPARTMENT: [
        'bölüm hakkında', 'program bilgisi', 'fakülte', 'akademik program',
        'lisans programı', 'yüksek lisans programı',
    ],
    INTENT_ADMISSION: [
        'başvuru koşulları', 'taban puan', 'kontenjan', 'yks puanı',
        'kayıt gereklilikleri', 'kabul şartları', 'burs imkânları',
    ],
    INTENT_CONTACT: [
        'iletişim bilgileri', 'telefon numarası', 'e-posta adresi',
        'ofis adresi', 'nasıl ulaşılır',
    ],
    INTENT_ANNOUNCEMENT: [
        'son haberler', 'güncel duyurular', 'etkinlikler', 'akademik takvim',
    ],
    INTENT_GENERAL: [],
}

_GLOBAL_QUERY_EXPANSIONS = {
    'kampüs': ['campus', 'yerleşke', 'facilities', 'library', 'transportation', 'student life'],
    'kampus': ['campus', 'yerleşke', 'facilities', 'library', 'transportation', 'student life'],
    'bölüm': ['department', 'program', 'faculty'],
    'bolum': ['department', 'program', 'faculty'],
    'ders': ['course', 'curriculum', 'akts', 'ects'],
    'iletişim': ['contact', 'telefon', 'email', 'address'],
    'iletisim': ['contact', 'telefon', 'email', 'address'],
    'başvuru': ['admission', 'application', 'requirements', 'scholarship'],
    'basvuru': ['admission', 'application', 'requirements', 'scholarship'],
}


def expand_query(question: str, intent: str) -> str:
    """Append intent-specific expansion terms to the query."""
    terms = list(_QUERY_EXPANSIONS.get(intent, []))
    normalized = normalize_text(question)
    for trigger, expansions in _GLOBAL_QUERY_EXPANSIONS.items():
        if trigger in normalized:
            terms.extend(expansions)
    if not terms:
        return question
    unique_terms = list(dict.fromkeys(terms))
    return question + ' ' + ' '.join(unique_terms)


def extract_relevant_snippet(text: str, keywords: list[str], max_chars: int = 1600) -> str:
    if not text:
        return ''
    if not keywords:
        return text[:max_chars]

    lines = [line.strip() for line in text.split('\n') if line.strip()]
    scored_lines = []
    for line in lines:
        lowered = normalize_text(line)
        score = sum(lowered.count(kw) for kw in keywords)
        if score:
            scored_lines.append((score, line))

    if scored_lines:
        scored_lines.sort(key=lambda item: item[0], reverse=True)
        snippet = '\n'.join(line for _, line in scored_lines[:12])
        return snippet[:max_chars]

    normalized_text = normalize_text(text)
    first_pos = min((normalized_text.find(kw) for kw in keywords if kw in normalized_text), default=-1)
    if first_pos != -1:
        start = max(0, first_pos - 300)
        return text[start:start + max_chars]

    return text[:max_chars]


def score_page_relevance(question: str, page, keywords: list[str]) -> int:
    title = normalize_text(page.title or '')
    description = normalize_text(page.description or '')
    text = normalize_text(page.text or '')
    url = normalize_text(page.url or '')
    question_text = normalize_text(question)
    asks_for_staff = is_staff_question(question)
    target_department = _primary_department(question)
    staff_keywords = {'akademik', 'kadro', 'kadrosu', 'kadrosunu', 'hoca', 'hocalar'}
    topical_keywords = [kw for kw in keywords if kw not in staff_keywords]

    score = 0
    for kw in keywords:
        score += title.count(kw) * 8
        score += url.count(kw) * 6
        score += description.count(kw) * 4
        score += text.count(kw) * 2

    for phrase, count in Counter(keywords).items():
        if len(phrase) > 4 and phrase in title:
            score += 10 * count
        if len(phrase) > 4 and phrase in url:
            score += 8 * count

    if question_text and question_text in text:
        score += 12

    if asks_for_staff:
        if '/akademik-kadro' in url or 'akademik kadro' in title:
            score += 50
        if '/ogrenci' in url or 'akademik takvim' in title:
            score -= 25
        for kw in topical_keywords:
            if kw in url:
                score += 20
            if kw in title:
                score += 12
        if target_department:
            haystack = f"{page.title} {page.url} {page.text[:1600]}"
            if _has_department_match(haystack, target_department):
                score += 80
            else:
                score -= 80
            if _mentions_other_department(haystack, target_department):
                score -= 40

    # Penalize generic or noisy landing pages for specific questions.
    if page.url.rstrip('/') == 'https://www.acibadem.edu.tr':
        score -= 20
    if '/anasayfa' in url:
        score -= 15
    if '/duyurular/' in url:
        score -= 8
    if page.depth <= 1:
        score -= 4

    if '/akademik/' in url:
        score += 6
    if '/bolumler/' in url:
        score += 12
    if '/hakkinda' in url or '/akademik-kadro' in url or '/bolum-baskaninin-mesaji' in url:
        score += 4

    # Boost faculty/department listing pages for listing questions
    if any(kw in ('fakülte', 'fakulte', 'fakülteler') for kw in keywords):
        if '/fakulte' in url or 'fakülte' in title:
            score += 20
        # Main listing pages that aggregate all faculties
        if url.rstrip('/').endswith('/fakulteler') or url.rstrip('/').endswith('/akademik'):
            score += 30

    # For "which departments" listing questions, strongly boost faculty-level overview pages
    # and demote individual deep department detail pages
    listing_q = is_listing_question(question)
    asks_for_bolum = any(kw in ('bölüm', 'bolum', 'bölümler', 'bolumler', 'program', 'programlar') for kw in keywords)
    if listing_q and asks_for_bolum:
        # Count meaningful URL path segments to identify overview vs sub-pages
        # e.g. /akademik/lisans/tip-fakultesi/ → 3 segments (overview)
        # vs   /akademik/lisans/tip-fakultesi/akademik-kadro → 4 segments (sub-page)
        path_segments = len([s for s in page.url.split('/') if s]) - 2  # subtract scheme + domain
        is_faculty_overview = (
            '/lisans/' in url and
            '/bolumler/' not in url and
            path_segments <= 3
        )
        if is_faculty_overview:
            score += 40
        # Also boost the top-level lisans page
        if url.rstrip('/').endswith('/lisans') or url.rstrip('/').endswith('/akademik/lisans'):
            score += 50
        # Demote individual deep department pages — they only describe one department
        if '/bolumler/' in url and path_segments >= 4:
            score -= 15

    intent = detect_intent(question)
    if intent == INTENT_CAMPUS:
        if any(term in url for term in ('kampus', 'kampüs', 'kampus-olanaklari', 'acu-da-yasam')):
            score += 35
        if any(term in title for term in ('kampüs', 'yerleşke', 'kütüphane', 'ulaşım')):
            score += 25
    if intent == INTENT_CONTACT:
        if any(term in url for term in ('iletisim', 'kampus-ziyaretleri', 'ulasim')):
            score += 35
        if any(term in title for term in ('iletişim', 'adres', 'ulaşım', 'kampüs ziyaretleri')):
            score += 25
        if '/duyurular/' in url or '/haberler/' in url or '/etkinlikler/' in url:
            score -= 40
    if intent in (INTENT_COURSE, INTENT_DEPARTMENT):
        if 'obs.acibadem.edu.tr' in url:
            score += 20

    return score


def find_relevant_urls(question: str, max_results: int = 3) -> list[str]:
    """Search URLIndex for pages relevant to the question."""
    from scraper.models import URLIndex

    keywords = extract_keywords(question)
    if not keywords:
        return []
    asks_for_staff = is_staff_question(question)

    query = Q()
    for kw in keywords:
        query |= Q(path_keywords__icontains=kw)
        query |= Q(title__icontains=kw)
        query |= Q(category__icontains=kw)

    return list(URLIndex.objects.filter(query).values_list('url', flat=True)[:max_results])


def fetch_page_text(url: str, max_chars: int = 3000) -> str:
    """
    Fetch a URL and return clean plain text from its main content.
    Updates the URL title in the index as a side effect.
    """
    try:
        resp = requests.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding

        soup = BeautifulSoup(resp.content, 'html.parser')

        # Remove non-content tags
        for tag in soup(['nav', 'footer', 'script', 'style', 'header',
                         'aside', 'form', 'button', 'noscript']):
            tag.decompose()

        # Prefer semantic content containers
        main = (
            soup.find('main') or
            soup.find(id='content') or
            soup.find(id='main-content') or
            soup.find(class_='content') or
            soup.find(class_='main-content') or
            soup.find('article') or
            soup.find('body')
        )

        text = main.get_text(separator='\n', strip=True) if main else ''
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        clean_text = '\n'.join(lines)

        # Cache the page title back into URLIndex
        title_tag = soup.find('title')
        if title_tag and title_tag.text.strip():
            try:
                from scraper.models import URLIndex
                URLIndex.objects.filter(url=url).update(
                    title=title_tag.text.strip()[:500],
                    last_fetched=timezone.now(),
                )
            except Exception:
                pass

        return clean_text[:max_chars]

    except requests.RequestException as e:
        logger.warning(f"Could not fetch {url}: {e}")
        return ''


def score_bologna_relevance(program, keywords: list[str]) -> int:
    """Score a BolognaProgram by how closely it matches the keywords."""
    score = 0
    name = normalize_text(program.program_name or '')
    dept = normalize_text(program.department or '')
    faculty = normalize_text(program.faculty or '')
    content = normalize_text(program.content or '')

    for kw in keywords:
        # Strong match: keyword is in the program/page name or department
        if kw in name:
            score += 20
        if kw in dept:
            score += 15
        if kw in faculty:
            score += 10
        # Weak match: keyword only appears in content body
        score += content.count(kw) * 1

    # Boost general info pages when the question has no specific program keyword.
    # These pages (Üniversite Hakkında, Kampüs, etc.) are curated top-level summaries.
    general_info_pages = {
        'üniversite hakkında', 'kampüs', 'konaklama', 'yemek hizmetleri',
        'spor ve sosyal yaşam', 'öğrenci kulüpleri', 'sağlık hizmetleri',
        'engelli öğrenci hizmetleri', 'iletişim ve ulaşım', 'yönetim',
        'bologna süreci', 'akts kataloğu',
    }
    if name in general_info_pages and score > 0:
        score += 50

    return score


def search_bologna(question: str, max_results: int = 2) -> list[dict]:
    """Search pre-scraped Bologna data for relevant academic program info."""
    from scraper.models import BolognaProgram

    keywords = extract_keywords(question)
    bologna_q = is_bologna_question(question)

    # If the question is explicitly about Bologna but has no usable keywords,
    # serve the curated general Bologna pages directly.
    if not keywords and bologna_q:
        fallback = BolognaProgram.objects.filter(
            program_name__in=['Bologna Süreci', 'AKTS Kataloğu', 'Üniversite Hakkında']
        )
        return [
            {'program': p.program_name, 'faculty': p.faculty, 'content': p.content, 'url': p.url}
            for p in fallback
        ]

    if not keywords:
        return []

    query = Q()
    for kw in keywords:
        query |= Q(faculty__icontains=kw)
        query |= Q(department__icontains=kw)
        query |= Q(program_name__icontains=kw)
        query |= Q(content__icontains=kw)

    candidates = list(BolognaProgram.objects.filter(query))

    # If this is a Bologna question but no keyword match found, broaden to general Bologna pages
    if not candidates and bologna_q:
        logger.info("Bologna question with no keyword match — serving general Bologna pages")
        fallback = BolognaProgram.objects.filter(
            program_name__in=['Bologna Süreci', 'AKTS Kataloğu', 'Üniversite Hakkında']
        )
        return [
            {'program': p.program_name, 'faculty': p.faculty, 'content': p.content, 'url': p.url}
            for p in fallback
        ]

    # Sort by relevance: name/department matches rank above content-only matches
    candidates.sort(key=lambda p: score_bologna_relevance(p, keywords), reverse=True)

    programs = candidates[:max_results]
    results = []
    for p in programs:
        # For short content (general info pages) return everything.
        # For long content (full course lists) extract the relevant snippet.
        if len(p.content) <= 3000:
            content = p.content
        else:
            content = extract_relevant_snippet(p.content, keywords, max_chars=1800)
        results.append({
            'program': p.program_name,
            'faculty': p.faculty,
            'content': content,
            'url': p.url,
        })
    return results


def search_scraped_pages(question: str, max_results: int = 3) -> list[dict]:
    """Search pre-loaded ACU website pages from the database."""
    from scraper.models import ScrapedPage

    intent = detect_intent(question)
    expanded_question = expand_query(question, intent)
    keywords = extract_keywords(expanded_question)
    if not keywords:
        return []
    asks_for_staff = is_staff_question(question)
    target_department = _primary_department(question)
    target_faculty = _primary_faculty(question)
    listing_q = is_listing_question(question)
    asks_for_bolum = any(kw in ('bölüm', 'bolum', 'bölümler', 'bolumler', 'program', 'programlar') for kw in keywords)

    if asks_for_staff and target_department:
        targeted_results = _search_targeted_staff_pages(target_department, max_results=max_results)
        if targeted_results:
            return targeted_results
    if is_head_question(question) and target_department:
        targeted_results = _search_targeted_head_pages(target_department, max_results=max_results)
        if targeted_results:
            return targeted_results
    if intent in (INTENT_COURSE, INTENT_BOLOGNA) and target_department:
        targeted_results = _search_targeted_course_pages(
            target_department,
            extract_course_codes(question),
            max_results=max_results,
        )
        if targeted_results:
            return targeted_results
    if listing_q and asks_for_bolum and target_faculty:
        targeted_results = _search_targeted_faculty_department_pages(target_faculty, max_results=max_results)
        if targeted_results:
            return targeted_results

    query = Q()
    for kw in keywords:
        query |= Q(title__icontains=kw)
        query |= Q(url__icontains=kw)
        query |= Q(text__icontains=kw)
        query |= Q(description__icontains=kw)

    # For "which departments/programs" listing questions, also pull in faculty-level
    # overview pages (they list all departments but may not contain the keyword "bölümler")
    if listing_q and asks_for_bolum:
        query |= Q(url__icontains='/lisans/', depth__lte=2)

    candidates = list(ScrapedPage.objects.filter(query))
    if not candidates:
        return []

    ranked_pages = sorted(
        candidates,
        key=lambda page: score_page_relevance(question, page, keywords),
        reverse=True,
    )

    pages = []
    for page in ranked_pages:
        if score_page_relevance(question, page, keywords) <= 0:
            continue
        pages.append(page)
        if len(pages) >= max_results:
            break

    return [
        {
            'url': p.url,
            'title': p.title,
            'text': (
                p.text[:2200]
                if asks_for_staff and '/akademik-kadro' in p.url.lower()
                else extract_relevant_snippet(p.text, keywords, max_chars=2200)
            ),
        }
        for p in pages
    ]


def _load_or_scrape_page(url: str):
    from scraper.acu_scraper import extract_page_payload
    from scraper.models import ScrapedPage

    page = ScrapedPage.objects.filter(url=url).first()
    if page and page.text.strip():
        return page

    payload = extract_page_payload(url)
    if not payload:
        return None

    page, _ = ScrapedPage.objects.update_or_create(
        url=url,
        defaults={
            'title': payload['title'],
            'description': payload['description'],
            'keywords': payload['keywords'],
            'text': payload['text'],
            'lang': payload['lang'],
            'source': payload['source'],
            'depth': payload['depth'],
            'scraped_at': payload['last_fetched'].isoformat(),
        },
    )
    return page


def _search_targeted_staff_pages(department: str, max_results: int = 3) -> list[dict]:
    from scraper.models import URLIndex

    slugs = _department_slug_variants(department)
    if not slugs:
        return []

    query = Q()
    for slug in slugs:
        query |= Q(url__icontains=slug)

    indexed_urls = list(
        URLIndex.objects
        .filter(query)
        .filter(Q(url__icontains='/akademik-kadro') | Q(url__icontains='/academic-staff'))
        .values_list('url', flat=True)[: max_results * 3]
    )

    results = []
    title_prefix = _department_display_name(department)
    for url in indexed_urls:
        page = _load_or_scrape_page(url)
        if not page:
            continue
        haystack = f"{page.title} {page.url} {page.text[:2000]}"
        if not _has_department_match(haystack, department):
            continue
        results.append({
            'url': page.url,
            'title': f"{title_prefix} Akademik Kadro",
            'text': page.text[:2200],
        })
        if len(results) >= max_results:
            break

    return results


def _search_targeted_head_pages(department: str, max_results: int = 3) -> list[dict]:
    from scraper.models import URLIndex

    slugs = _department_slug_variants(department)
    if not slugs:
        return []

    query = Q()
    for slug in slugs:
        query |= Q(url__icontains=slug)

    indexed_urls = list(
        URLIndex.objects
        .filter(query)
        .filter(url__icontains='/bolum-baskaninin-mesaji')
        .values_list('url', flat=True)[: max_results * 3]
    )

    results = []
    title_prefix = _department_display_name(department)
    for url in indexed_urls:
        page = _load_or_scrape_page(url)
        if not page:
            continue
        haystack = f"{page.title} {page.url} {page.text[:2000]}"
        if not _has_department_match(haystack, department):
            continue
        results.append({
            'url': page.url,
            'title': f"{title_prefix} Bölüm Başkanı",
            'text': page.text[:2200],
        })
        if len(results) >= max_results:
            break

    return results


def _search_targeted_faculty_department_pages(faculty: str, max_results: int = 6) -> list[dict]:
    from scraper.models import URLIndex

    slugs = _faculty_slug_variants(faculty)
    if not slugs:
        return []

    query = Q()
    for slug in slugs:
        query |= Q(url__icontains=slug)

    indexed_urls = list(
        URLIndex.objects
        .filter(query)
        .filter(Q(url__icontains='/bolumler/') | Q(url__icontains='/departments/'))
        .values_list('url', flat=True)
    )

    results = []
    seen_departments = set()
    faculty_title = _faculty_display_name(faculty)
    for url in indexed_urls:
        page = _load_or_scrape_page(url)
        if not page:
            continue
        department = infer_department(page.title, page.text, page.url)
        if not department or department in seen_departments:
            continue
        seen_departments.add(department)
        results.append({
            'url': page.url,
            'title': f"{faculty_title} - {department}",
            'text': page.text[:2200],
        })
        if len(results) >= max_results:
            break

    return results


def _fetch_bologna_course_row(program_url: str, course_code: str) -> dict | None:
    cur_sunit = parse_qs(urlparse(program_url).query).get('curSunit', [''])[0]
    if not cur_sunit:
        return None

    courses_url = f'https://obs.acibadem.edu.tr/oibs/bologna/progCourses.aspx?lang=tr&curSunit={cur_sunit}'
    try:
        response = requests.get(courses_url, timeout=20, headers=HEADERS)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Could not fetch Bologna course page %s: %s", courses_url, exc)
        return None

    soup = BeautifulSoup(response.content, 'html.parser')
    target_code = _format_course_code(course_code)
    target_normalized = target_code.replace(' ', '')

    for row in soup.find_all('tr'):
        cells = [cell.get_text(' ', strip=True) for cell in row.find_all('td')]
        if len(cells) < 6:
            continue

        row_code = _format_course_code(cells[1] if len(cells) > 1 else '')
        if row_code.replace(' ', '') != target_normalized:
            continue

        return {
            'code': row_code,
            'name': cells[2] if len(cells) > 2 else '',
            'tul': cells[3] if len(cells) > 3 else '',
            'kind': cells[4] if len(cells) > 4 else '',
            'akts': cells[5] if len(cells) > 5 else '',
            'group_count': cells[6] if len(cells) > 6 else '',
            'teaching_mode': cells[7] if len(cells) > 7 else '',
            'url': courses_url,
        }

    return None


def _search_targeted_course_pages(
    department: str,
    course_codes: list[str],
    max_results: int = 3,
) -> list[dict]:
    from scraper.models import BolognaProgram

    if not department or not course_codes:
        return []

    display_name = _department_display_name(department)
    aliases = DEPARTMENT_ALIASES.get(display_name, (display_name.lower(),))

    query = Q()
    for alias in aliases:
        query |= Q(program_name__icontains=alias)
        query |= Q(department__icontains=alias)

    candidates = list(BolognaProgram.objects.filter(query))
    if not candidates:
        return []

    results = []
    for program in candidates:
        for course_code in course_codes:
            details = _fetch_bologna_course_row(program.url, course_code)
            if not details:
                continue
            content = '\n'.join([
                f"Bölüm: {display_name}",
                f"Ders Kodu: {details['code']}",
                f"Ders Adı: {details['name']}",
                f"T+U+L: {details['tul']}",
                f"Tür: {details['kind']}",
                f"AKTS: {details['akts']}",
                f"Öğretim Şekli: {details['teaching_mode']}",
            ]).strip()
            results.append({
                'url': details['url'],
                'title': f"{display_name} {details['code']} Ders Bilgisi",
                'text': content,
            })
            if len(results) >= max_results:
                return results

    return results


def _curated_scraped_fallback(intent: str, max_results: int = 3) -> list[dict]:
    from scraper.models import ScrapedPage

    if intent == INTENT_CAMPUS:
        query = (
            Q(url__icontains='kampus-olanaklari') |
            Q(url__icontains='kampus-ziyaretleri') |
            Q(url__icontains='surdurulebilir-kampus') |
            Q(title__icontains='Kütüphane') |
            Q(title__icontains='Spor Merkezi')
        )
    elif intent == INTENT_CONTACT:
        query = (
            Q(url__icontains='iletisim') |
            Q(url__icontains='ulasim') |
            Q(url__icontains='kampus-ziyaretleri') |
            Q(title__icontains='İletişim') |
            Q(title__icontains='Kampüs Ziyaretleri')
        )
    elif intent == INTENT_ADMISSION:
        query = (
            Q(url__icontains='aday') |
            Q(url__icontains='basvuru') |
            Q(title__icontains='Başvuru') |
            Q(title__icontains='Burs')
        )
    else:
        return []

    pages = list(
        ScrapedPage.objects.filter(query)
        .exclude(url__icontains='/etkinlikler/')
        .exclude(url__icontains='/duyurular/')
        .exclude(url__icontains='/haberler/')[:20]
    )
    ranked_pages = sorted(
        pages,
        key=lambda page: score_page_relevance(page.title + ' ' + page.url, page, extract_keywords(page.title + ' ' + page.url)),
        reverse=True,
    )

    return [
        {
            'url': p.url,
            'title': p.title,
            'text': extract_relevant_snippet(p.text, extract_keywords(p.title + ' ' + p.url), max_chars=2200),
        }
        for p in ranked_pages[:max_results]
    ]


def _supplement_semantic_candidates(question: str, intent: str) -> list[dict]:
    candidates: list[dict] = []

    if intent in (INTENT_BOLOGNA, INTENT_COURSE, INTENT_DEPARTMENT):
        for result in search_bologna(question, max_results=6):
            candidates.append({
                'text': result['content'],
                'title': f"{result['faculty']} - {result['program']}".strip(' -'),
                'url': result['url'],
                'source_type': 'bologna',
                'distance': 0.34,
                'page_type': 'course' if intent == INTENT_COURSE else 'academic',
                'section_type': 'bologna',
                'faculty': result['faculty'],
                'department': result['program'],
                'course_code': '',
                'language': 'tr',
                'last_updated': '',
                'is_stable': True,
                'is_noisy': False,
            })

    if intent in (INTENT_CAMPUS, INTENT_CONTACT, INTENT_ADMISSION, INTENT_STUDENT_LIFE):
        for result in search_scraped_pages(question, max_results=6):
            page_type = classify_page_type(result['url'], result['title'], result['text'])
            candidates.append({
                'text': result['text'],
                'title': result['title'],
                'url': result['url'],
                'source_type': 'scraped',
                'distance': 0.36,
                'page_type': page_type,
                'section_type': page_type,
                'faculty': '',
                'department': '',
                'course_code': '',
                'language': 'tr',
                'last_updated': '',
                'is_stable': page_type not in _NOISY_PAGE_TYPES,
                'is_noisy': page_type in _NOISY_PAGE_TYPES,
            })

    return candidates


def _chunks_have_embeddings() -> bool:
    """Quick check: does ContentChunk table have any embedded rows?"""
    try:
        from scraper.models import ContentChunk
        return ContentChunk.objects.filter(embedding__isnull=False).exists()
    except Exception:
        return False


def semantic_search(
    question: str,
    source_type: str | None = None,
    top_k: int = 8,
) -> list[dict]:
    """
    Embed the question with nomic-embed-text, then find the closest
    ContentChunk rows via pgvector cosine distance.

    Args:
        source_type: 'scraped', 'bologna', or None (search all).
        top_k: number of candidate chunks to retrieve before reranking.

    Returns list of dicts with keys: text, title, url, source_type, distance.
    """
    from scraper.models import ContentChunk
    from scraper.embedder import get_embedding
    from pgvector.django import CosineDistance

    embedding = get_embedding(question)
    if embedding is None:
        logger.warning("Could not get embedding for question — falling back to keyword search")
        return []

    qs = ContentChunk.objects.filter(embedding__isnull=False)
    if source_type:
        qs = qs.filter(source_type=source_type)

    chunks = (
        qs
        .annotate(distance=CosineDistance('embedding', embedding))
        .filter(distance__lte=SEMANTIC_DISTANCE_THRESHOLD)
        .order_by('distance')[:top_k]
    )

    return [
        {
            'text': c.chunk_text,
            'title': c.title,
            'url': c.source_url,
            'source_type': c.source_type,
            'distance': float(c.distance),
            'page_type': c.page_type,
            'section_type': c.section_type,
            'faculty': c.faculty,
            'department': c.department,
            'course_code': c.course_code,
            'language': c.language,
            'last_updated': c.last_updated.isoformat() if c.last_updated else '',
            'is_stable': c.is_stable,
            'is_noisy': c.is_noisy,
        }
        for c in chunks
    ]


def _question_language(question: str) -> str:
    normalized = normalize_text(question)
    if any(word in normalized for word in ('what', 'where', 'when', 'course', 'admission', 'contact')):
        return 'en'
    return 'tr'


def _metadata_for_chunk(chunk: dict) -> dict:
    text = chunk.get('text', '')
    title = chunk.get('title', '')
    url = chunk.get('url', '')
    page_type = chunk.get('page_type') or classify_page_type(url, title, text)
    faculty = chunk.get('faculty') or infer_faculty(title, text, url)
    department = chunk.get('department') or infer_department(title, text, url)
    return {
        'page_type': page_type,
        'section_type': chunk.get('section_type') or page_type,
        'faculty': faculty,
        'department': department,
        'course_code': (chunk.get('course_code') or '').upper(),
        'language': chunk.get('language') or 'tr',
        'last_updated': chunk.get('last_updated') or '',
        'is_stable': bool(chunk.get('is_stable', page_type not in _NOISY_PAGE_TYPES)),
        'is_noisy': bool(chunk.get('is_noisy', page_type in _NOISY_PAGE_TYPES)),
    }


def _keyword_score(text_lower: str, title_lower: str, keywords: list[str]) -> tuple[float, int]:
    kw_hits = sum(text_lower.count(kw) + title_lower.count(kw) * 4 for kw in keywords)
    return min(kw_hits / 24.0, 1.0), kw_hits


def _exact_match_score(chunk: dict, keywords: list[str], entities: dict[str, list[str]], intent: str) -> float:
    text = normalize_text(chunk.get('text', ''))
    title = normalize_text(chunk.get('title', ''))
    url = normalize_text(chunk.get('url', ''))
    meta = _metadata_for_chunk(chunk)
    score = 0.0

    for code in entities['course_codes']:
        code_lower = code.lower()
        if code_lower in text or code_lower in title or code_lower == meta['course_code'].lower():
            score += 1.0

    for faculty in entities['faculties']:
        if faculty in normalize_text(meta['faculty']) or faculty in title or faculty in url:
            score += 0.7

    for department in entities['departments']:
        if department in normalize_text(meta['department']) or department in title or department in url:
            score += 0.7

    keyword_set = set(keywords)
    if keyword_set & ADMISSION_TERMS and meta['page_type'] == 'admission':
        score += 0.5
    if keyword_set & CAMPUS_TERMS and meta['page_type'] in {'campus', 'student_life'}:
        score += 0.5
    if keyword_set & CONTACT_TERMS and meta['page_type'] == 'contact':
        score += 0.5
    if intent == INTENT_BOLOGNA and chunk.get('source_type') == 'bologna':
        score += 0.4

    return min(score, 1.5)


def _freshness_stability_score(chunk: dict) -> float:
    meta = _metadata_for_chunk(chunk)
    score = 0.15 if meta['is_stable'] else -0.15
    page_type = meta['page_type']
    if page_type in {'announcement', 'event', 'news', 'promo'}:
        score -= 0.35

    if meta['last_updated'] and page_type not in {'announcement', 'event', 'news'}:
        score += 0.05
    return score


def _intent_metadata_boost(chunk: dict, intent: str) -> float:
    meta = _metadata_for_chunk(chunk)
    page_type = meta['page_type']
    boost = 0.0

    if page_type in _INTENT_PREFERRED_PAGE_TYPES.get(intent, []):
        boost += 0.20
    if intent == INTENT_CAMPUS and page_type in {'campus', 'student_life'}:
        boost += 0.25
    if intent == INTENT_CONTACT and page_type == 'contact':
        boost += 0.30
    if intent == INTENT_ADMISSION and page_type == 'admission':
        boost += 0.25
    if intent == INTENT_DEPARTMENT and page_type in {'department', 'academic'}:
        boost += 0.20
    if intent == INTENT_COURSE and (page_type == 'course' or chunk.get('source_type') == 'bologna'):
        boost += 0.25
    if intent == INTENT_ANNOUNCEMENT and page_type in {'announcement', 'news', 'event'}:
        boost += 0.20
    return boost


def rerank(
    chunks: list[dict],
    keywords: list[str],
    intent: str = INTENT_GENERAL,
    entities: dict[str, list[str]] | None = None,
    top_n: int = 3,
) -> list[dict]:
    """
    Hybrid reranker: semantic score + keyword score + URL stability + intent boost.

    Final score = sem(0.55) + kw(0.25) + stability(0.20) + intent_boost

    Also logs score breakdown for every candidate for debugging.
    """
    if not chunks:
        return []

    entities = entities or {'course_codes': [], 'faculties': [], 'departments': []}
    penalise_noisy = intent != INTENT_ANNOUNCEMENT
    question_lang = _question_language(' '.join(keywords))

    filtered = []
    for chunk in chunks:
        meta = _metadata_for_chunk(chunk)
        if penalise_noisy and meta['page_type'] in _NOISY_PAGE_TYPES:
            continue
        if penalise_noisy and meta['is_noisy']:
            continue
        filtered.append(chunk)

    if len(filtered) >= top_n:
        chunks = filtered
    else:
        logger.info(f"[RERANK] metadata filter would leave only {len(filtered)} chunks — keeping broader set")

    for chunk in chunks:
        text_lower = chunk['text'].lower()
        title_lower = chunk['title'].lower()
        url = chunk['url']
        meta = _metadata_for_chunk(chunk)

        sem_score = max(0.0, 1.0 - chunk.get('distance', 0.5))
        kw_score, kw_hits = _keyword_score(text_lower, title_lower, keywords)
        exact_score = _exact_match_score(chunk, keywords, entities, intent)
        stability_score = _freshness_stability_score(chunk)
        intent_boost = _intent_metadata_boost(chunk, intent)
        lang_boost = 0.05 if meta['language'] in ('', question_lang) else -0.05
        url_bonus = 0.05 if any(kw in url.lower() for kw in keywords[:4]) else 0.0

        final_score = (
            sem_score * 0.38 +
            kw_score * 0.18 +
            exact_score * 0.22 +
            stability_score * 0.12 +
            intent_boost * 0.10
        ) + lang_boost + url_bonus

        chunk['_score'] = final_score
        chunk['_sem_score'] = sem_score
        chunk['_kw_score'] = kw_score
        chunk['_kw_hits'] = kw_hits
        chunk['_exact_score'] = exact_score
        chunk['_stability'] = stability_score
        chunk['_page_type'] = meta['page_type']
        chunk['_intent_boost'] = intent_boost
        chunk['_lang_boost'] = lang_boost
        chunk['_metadata'] = meta

    ranked = sorted(chunks, key=lambda c: c['_score'], reverse=True)

    # Debug logging — top 10 candidates with score breakdown
    logger.info(f"[RERANK] intent={intent} candidates={len(chunks)} → top {top_n}")
    for i, c in enumerate(ranked[:10]):
        logger.info(
            f"  #{i+1} score={c['_score']:.3f} "
            f"(sem={c['_sem_score']:.3f} kw={c['_kw_score']:.3f}/{c['_kw_hits']} "
            f"exact={c['_exact_score']:.2f} stab={c['_stability']:.2f} "
            f"boost={c['_intent_boost']:.2f} lang={c['_lang_boost']:.2f}) "
            f"type={c['_page_type']} | {c['url'][:80]}"
        )

    return ranked[:top_n]


def _deduplicate_chunks(chunks: list[dict]) -> list[dict]:
    """Remove near-duplicate chunks while limiting multiple chunks per URL."""
    seen = set()
    unique = []
    url_counts: dict[str, int] = {}
    for chunk in chunks:
        key = (chunk['url'], chunk['text'][:80])
        if key not in seen:
            if url_counts.get(chunk['url'], 0) >= 2:
                continue
            seen.add(key)
            unique.append(chunk)
            url_counts[chunk['url']] = url_counts.get(chunk['url'], 0) + 1
    return unique


def _top_chunks_are_strong(top_chunks: list[dict]) -> bool:
    if len(top_chunks) < 1:
        return False
    if any(chunk.get('_metadata', {}).get('is_noisy') for chunk in top_chunks):
        return False
    avg_score = mean(chunk.get('_score', 0.0) for chunk in top_chunks)
    best_score = max(chunk.get('_score', 0.0) for chunk in top_chunks)
    return best_score >= 0.52 and avg_score >= 0.40


def get_context_for_question(question: str) -> tuple[str, list[str]]:
    """
    Intent-aware retrieval pipeline.

    STEP 1 — Intent detection + query expansion
    STEP 2 — Semantic search (pgvector) with source_type routing
    STEP 3 — Hybrid rerank (sem + keyword + URL stability + intent boost) → top 3
    STEP 4 — Keyword fallback (when embeddings not yet generated)
    STEP 5 — Absolute fallback (general university pages)
    """
    intent   = detect_intent(question)
    keywords = extract_keywords(question)
    entities = extract_named_entities(question)
    listing  = is_listing_question(question)
    target_department = _primary_department(question)
    target_faculty = _primary_faculty(question)
    context_parts: list[str] = []
    sources:        list[str] = []

    logger.info(
        f"[RETRIEVAL] question='{question[:80]}' intent={intent} "
        f"keywords={keywords} entities={entities}"
    )

    if is_head_question(question) and target_department:
        logger.info("[RETRIEVAL] targeted head lookup for department=%s", target_department)
        for result in search_scraped_pages(question, max_results=3):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if intent in (INTENT_COURSE, INTENT_BOLOGNA) and target_department and entities['course_codes']:
        logger.info(
            "[RETRIEVAL] targeted course lookup for department=%s course_codes=%s",
            target_department,
            entities['course_codes'],
        )
        for result in search_scraped_pages(question, max_results=3):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if intent == INTENT_STAFF and target_department:
        logger.info("[RETRIEVAL] targeted staff lookup for department=%s", target_department)
        for result in search_scraped_pages(question, max_results=3):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    asks_for_departments = any(
        kw in ('bölüm', 'bolum', 'bölümler', 'bolumler', 'program', 'programlar')
        for kw in keywords
    )
    if listing and asks_for_departments and target_faculty:
        logger.info("[RETRIEVAL] targeted faculty listing for faculty=%s", target_faculty)
        for result in _search_targeted_faculty_department_pages(target_faculty, max_results=8):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    # ------------------------------------------------------------------ #
    # STEP 2 — Semantic search (pgvector)                                  #
    # ------------------------------------------------------------------ #
    if _chunks_have_embeddings():
        top_k = 18 if listing else 14
        expanded_q = expand_query(question, intent)

        logger.info(f"[RETRIEVAL] expanded_query='{expanded_q[:120]}'")

        # Source routing: Bologna-only, scraped-only, or all
        if intent == INTENT_BOLOGNA:
            source_type = 'bologna'
        elif intent in (INTENT_ADMISSION, INTENT_CONTACT, INTENT_ANNOUNCEMENT):
            source_type = 'scraped'
        else:
            # Campus, student_life, course, department, general, staff → search both
            # Bologna has curated campus/program pages that are often better than scraped
            source_type = None

        candidates = semantic_search(expanded_q, source_type=source_type, top_k=top_k)
        candidates.extend(_supplement_semantic_candidates(question, intent))
        candidates = _deduplicate_chunks(candidates)

        logger.info(f"[RETRIEVAL] semantic candidates after dedup: {len(candidates)}")
        for i, candidate in enumerate(candidates[:10]):
            logger.info(
                f"[RETRIEVAL] top10 #{i+1} dist={candidate.get('distance', 1.0):.3f} "
                f"type={candidate.get('page_type') or classify_page_type(candidate['url'], candidate['title'], candidate['text'])} "
                f"url={candidate['url'][:100]}"
            )

        top_chunks = rerank(candidates, keywords, intent=intent, entities=entities, top_n=3)
        if not _top_chunks_are_strong(top_chunks):
            logger.warning("[RETRIEVAL] top chunks are weak/noisy; refusing to ground answer on them")
            top_chunks = []

        for chunk in top_chunks:
            label = chunk['title'] or chunk['url']
            context_parts.append(f"=== {label} ===\n{chunk['text']}")
            if chunk['url'] not in sources:
                sources.append(chunk['url'])

        logger.info(f"[RETRIEVAL] final chunks selected: {len(top_chunks)}")
        for c in top_chunks:
            logger.info(f"  → score={c['_score']:.3f} | {c['url'][:80]}")

    # ------------------------------------------------------------------ #
    # STEP 3 — Keyword fallback (embeddings not ready yet)                 #
    # ------------------------------------------------------------------ #
    if not context_parts:
        logger.info("[RETRIEVAL] semantic empty — keyword fallback")

        if intent == INTENT_BOLOGNA:
            for result in search_bologna(question, max_results=4 if listing else 2):
                context_parts.append(
                    f"=== {result['faculty']} - {result['program']} ===\n{result['content']}"
                )
                if result['url'] not in sources:
                    sources.append(result['url'])

        elif intent in (INTENT_ADMISSION, INTENT_CONTACT, INTENT_ANNOUNCEMENT,
                        INTENT_CAMPUS, INTENT_STUDENT_LIFE):
            curated_results = _curated_scraped_fallback(intent, max_results=3)
            for result in curated_results:
                if result['url'] not in sources:
                    context_parts.append(f"=== {result['title']} ===\n{result['text']}")
                    sources.append(result['url'])

            if not curated_results:
                for result in search_scraped_pages(question, max_results=4 if listing else 2):
                    if result['url'] not in sources:
                        context_parts.append(f"=== {result['title']} ===\n{result['text']}")
                        sources.append(result['url'])

            if not context_parts:
                for url in find_relevant_urls(question, max_results=3 if listing else 2):
                    if url in sources:
                        continue
                    text = fetch_page_text(url)
                    if text:
                        context_parts.append(f"=== Kaynak: {url} ===\n{text}")
                        sources.append(url)
                    time.sleep(0.5)

        else:
            # Ambiguous / general: merge Bologna + ScrapedPage
            for result in search_bologna(question, max_results=4 if listing else 2):
                context_parts.append(
                    f"=== {result['faculty']} - {result['program']} ===\n{result['content']}"
                )
                if result['url'] not in sources:
                    sources.append(result['url'])

            for result in search_scraped_pages(question, max_results=4 if listing else 2):
                if result['url'] not in sources:
                    context_parts.append(f"=== {result['title']} ===\n{result['text']}")
                    sources.append(result['url'])

            if not context_parts:
                for url in find_relevant_urls(question, max_results=3 if listing else 2):
                    if url in sources:
                        continue
                    text = fetch_page_text(url)
                    if text:
                        context_parts.append(f"=== Kaynak: {url} ===\n{text}")
                        sources.append(url)
                    time.sleep(0.5)

    # ------------------------------------------------------------------ #
    # STEP 4 — Absolute fallback: curated general university pages         #
    # ------------------------------------------------------------------ #
    if not context_parts:
        logger.info("[RETRIEVAL] absolute fallback — serving default university info pages")
        from scraper.models import BolognaProgram
        for p in BolognaProgram.objects.filter(
            program_name__in=['Üniversite Hakkında', 'Kampüs', 'Bologna Süreci']
        ):
            context_parts.append(f"=== {p.faculty} - {p.program_name} ===\n{p.content}")
            sources.append(p.url)

    return '\n\n'.join(context_parts), sources
