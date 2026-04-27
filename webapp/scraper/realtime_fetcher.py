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

CANONICAL_CONTACT_URL = 'https://www.acibadem.edu.tr/iletisim'
CONTACT_FALLBACK_URLS = (
    'https://www.acibadem.edu.tr/iletisim',
    'https://www.acibadem.edu.tr/en/contact',
)

EXCHANGE_TERMS = {
    'erasmus', 'erasmus+', 'exchange', 'değişim', 'degisim',
    'değişim programı', 'degisim programi', 'partner üniversite',
    'partner universite', 'partner university', 'institutional agreement',
    'institutional agreements', 'ikili anlaşma', 'ikili anlasma',
    'global exchange', 'learning mobility', 'traineeship mobility',
    'öğrenim hareketliliği', 'ogrenim hareketliligi',
    'staj hareketliliği', 'staj hareketliligi',
}

HEAD_KEYWORDS = {
    'bölüm başkanı', 'bolum baskani', 'anabilim dalı başkanı', 'program başkanı',
    'program baskani', 'chair', 'head of department', 'director',
}

COURSE_CODE_PREFIX_MAP = {
    'CSE': 'bilgisayar mühendisliği',
    'MBG': 'moleküler biyoloji ve genetik',
    'PSY': 'psikoloji',
    'NUR': 'hemşirelik',
    'PHR': 'eczacılık',
    'BME': 'biyomedikal mühendisliği',
    'NTD': 'beslenme ve diyetetik',
    'BCP': 'bilgisayar programcılığı',
    'MED': 'tıp',
    'ECZ': 'eczacılık',
    'PSI': 'psikoloji',
    'HEM': 'hemşirelik',
    'BES': 'beslenme ve diyetetik',
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

_CATALOG_CACHE_TTL_SECONDS = 600
_department_catalog_cache: tuple[float, dict[str, tuple[str, ...]]] | None = None
_department_slug_cache: tuple[float, dict[str, tuple[str, ...]]] | None = None
_faculty_catalog_cache: tuple[float, dict[str, tuple[str, ...]]] | None = None
_faculty_slug_cache: tuple[float, dict[str, tuple[str, ...]]] | None = None


def normalize_text(text: str) -> str:
    text = unicodedata.normalize('NFKC', (text or '')).lower()
    return re.sub(r'\s+', ' ', text).strip()


def _ascii_fold(text: str) -> str:
    replacements = str.maketrans({
        'ç': 'c', 'ğ': 'g', 'ı': 'i', 'İ': 'i', 'ö': 'o', 'ş': 's', 'ü': 'u',
    })
    return normalize_text(text).translate(replacements).replace('\u0307', '')


def _normalize_compact(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', _ascii_fold(text))


def _slugify_value(text: str) -> str:
    slug = _ascii_fold(text)
    slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')
    return re.sub(r'-{2,}', '-', slug)


def _program_name_variants(name: str) -> set[str]:
    variants = set()
    if not name:
        return variants

    name = re.sub(r'\s+', ' ', name).strip()
    if not name:
        return variants

    variants.add(name)
    ascii_name = _ascii_fold(name)
    if ascii_name:
        variants.add(ascii_name)

    # Common question phrasings omit suffixes like "programı" / "bölümü"
    shortened = re.sub(
        r'\b(programı|programi|program|bölümü|bolumu|bölüm|bolum|anabilim dalı|anabilim dali|abd|ad)\b',
        '',
        name,
        flags=re.IGNORECASE,
    )
    shortened = re.sub(r'\s+', ' ', shortened).strip(' -/')
    if shortened and shortened != name:
        variants.add(shortened)
        variants.add(_ascii_fold(shortened))

    for variant in list(variants):
        compact = variant.strip()
        if compact:
            variants.add(compact)

    return {variant for variant in variants if variant and len(variant) > 1}


def _merge_alias_maps(
    base_aliases: dict[str, tuple[str, ...]],
    extra_names: set[str],
) -> dict[str, tuple[str, ...]]:
    merged: dict[str, set[str]] = {
        canonical: set(aliases) | _program_name_variants(canonical)
        for canonical, aliases in base_aliases.items()
    }

    for name in sorted(extra_names):
        canonical = next((label for label in merged if _ascii_fold(label) == _ascii_fold(name)), name)
        merged.setdefault(canonical, set()).update(_program_name_variants(name))
        merged[canonical].update(_program_name_variants(canonical))

    return {
        canonical: tuple(sorted(aliases, key=lambda item: (len(item), item)))
        for canonical, aliases in merged.items()
    }


def _merge_slug_maps(
    alias_map: dict[str, tuple[str, ...]],
    base_slugs: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    merged: dict[str, set[str]] = {
        canonical: set(slugs)
        for canonical, slugs in base_slugs.items()
    }

    for canonical, aliases in alias_map.items():
        slug_set = merged.setdefault(canonical, set())
        slug_set.add(_slugify_value(canonical))
        for alias in aliases:
            slug = _slugify_value(alias)
            if slug:
                slug_set.add(slug)

    return {
        canonical: tuple(sorted(slugs))
        for canonical, slugs in merged.items()
    }


def _load_dynamic_catalog_names() -> tuple[set[str], set[str]]:
    departments: set[str] = set()
    faculties: set[str] = set()

    try:
        from scraper.models import BolognaProgram

        for faculty, department, program_name in BolognaProgram.objects.values_list(
            'faculty', 'department', 'program_name'
        ):
            if faculty:
                faculties.add(faculty.strip())
            if department:
                departments.add(department.strip())
            if program_name:
                departments.add(program_name.strip())
    except Exception as exc:
        logger.info("[CATALOG] Could not load dynamic Bologna catalog: %s", exc)

    try:
        from scraper.models import ContentChunk

        for faculty, department in ContentChunk.objects.values_list('faculty', 'department').distinct():
            if faculty:
                faculties.add(faculty.strip())
            if department:
                departments.add(department.strip())
    except Exception:
        pass

    return departments, faculties


def _get_department_aliases() -> dict[str, tuple[str, ...]]:
    global _department_catalog_cache
    now = time.time()
    if _department_catalog_cache and now - _department_catalog_cache[0] < _CATALOG_CACHE_TTL_SECONDS:
        return _department_catalog_cache[1]

    dynamic_departments, _ = _load_dynamic_catalog_names()
    aliases = _merge_alias_maps(DEPARTMENT_ALIASES, dynamic_departments)
    _department_catalog_cache = (now, aliases)
    return aliases


def _get_department_slugs() -> dict[str, tuple[str, ...]]:
    global _department_slug_cache
    now = time.time()
    if _department_slug_cache and now - _department_slug_cache[0] < _CATALOG_CACHE_TTL_SECONDS:
        return _department_slug_cache[1]

    slugs = _merge_slug_maps(_get_department_aliases(), DEPARTMENT_URL_SLUGS)
    _department_slug_cache = (now, slugs)
    return slugs


def _get_faculty_aliases() -> dict[str, tuple[str, ...]]:
    global _faculty_catalog_cache
    now = time.time()
    if _faculty_catalog_cache and now - _faculty_catalog_cache[0] < _CATALOG_CACHE_TTL_SECONDS:
        return _faculty_catalog_cache[1]

    _, dynamic_faculties = _load_dynamic_catalog_names()
    aliases = _merge_alias_maps(FACULTY_ALIASES, dynamic_faculties)
    _faculty_catalog_cache = (now, aliases)
    return aliases


def _get_faculty_slugs() -> dict[str, tuple[str, ...]]:
    global _faculty_slug_cache
    now = time.time()
    if _faculty_slug_cache and now - _faculty_slug_cache[0] < _CATALOG_CACHE_TTL_SECONDS:
        return _faculty_slug_cache[1]

    slugs = _merge_slug_maps(_get_faculty_aliases(), FACULTY_URL_SLUGS)
    _faculty_slug_cache = (now, slugs)
    return slugs


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


def _department_from_course_codes(course_codes: list[str]) -> str:
    """Infer the department name from known course code prefixes (e.g. CSE → bilgisayar mühendisliği)."""
    for code in course_codes:
        prefix_match = re.match(r'^([A-Z]+)', code.upper())
        if prefix_match:
            dept = COURSE_CODE_PREFIX_MAP.get(prefix_match.group(1), '')
            if dept:
                return dept
    return ''


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
    for label, aliases in _get_department_aliases().items():
        if any(_contains_fuzzy_phrase(text, alias, threshold=0.84) for alias in aliases):
            hits.append(label.lower())
    return hits


def _primary_department(question: str) -> str:
    matches = _find_department_mentions(question)
    return matches[0] if matches else ''


def _primary_faculty(question: str) -> str:
    normalized = normalize_text(question)
    for label, aliases in _get_faculty_aliases().items():
        if any(_contains_fuzzy_phrase(normalized, alias, threshold=0.84) for alias in aliases):
            return label.lower()
    return ''


def _is_general_university_address_question(question: str) -> bool:
    normalized = _ascii_fold(question)
    if not any(term in normalized for term in ('adres', 'address', 'nerede', 'nerde', 'konum', 'lokasyon')):
        return False

    # Avoid overriding specific faculty/department/unit address requests.
    if _primary_department(question) or _primary_faculty(question):
        return False

    unit_markers = (
        'fakulte', 'bolum', 'enstitu', 'myo', 'ofis', 'ogrenci isleri',
        'kutuphane', 'laboratuvar', 'dekanlik', 'rektorluk',
    )
    if any(marker in normalized for marker in unit_markers):
        return False

    university_markers = (
        'acibadem universitesi', 'acibadem universite', 'universite', 'kampus',
    )
    return any(marker in normalized for marker in university_markers)


def _address_line_score(line: str) -> int:
    lowered = _ascii_fold(line)
    score = 0
    if 'kampus' in lowered or 'campus' in lowered:
        score += 4
    if 'cad' in lowered or 'caddesi' in lowered or 'street' in lowered:
        score += 3
    if 'no:' in lowered or 'no ' in lowered:
        score += 3
    if '/' in line:
        score += 2
    if any(city in lowered for city in ('istanbul', 'atasehir', 'kayisdagi')):
        score += 3
    if re.search(r'\bno[: ]\s*\d+', lowered):
        score += 4
    return score


def _extract_address_block_from_text(text: str) -> str:
    if not text:
        return ''

    raw_lines = [line.strip(' \t,;') for line in text.splitlines() if line.strip()]
    if not raw_lines:
        return ''

    best_block = ''
    best_score = 0
    for idx, line in enumerate(raw_lines):
        score = _address_line_score(line)
        if score <= 0:
            continue

        block_lines = [line]
        total_score = score

        previous = raw_lines[idx - 1] if idx > 0 else ''
        if previous and _address_line_score(previous) >= 3:
            block_lines.insert(0, previous)
            total_score += _address_line_score(previous)

        for next_idx in range(idx + 1, min(idx + 3, len(raw_lines))):
            next_line = raw_lines[next_idx]
            next_score = _address_line_score(next_line)
            if next_score < 2:
                break
            block_lines.append(next_line)
            total_score += next_score

        deduped_lines = list(dict.fromkeys(block_lines))
        candidate = '\n'.join(deduped_lines)
        if total_score > best_score:
            best_score = total_score
            best_block = candidate

    return best_block


def _fetch_live_university_address() -> str:
    for url in CONTACT_FALLBACK_URLS:
        try:
            response = requests.get(url, timeout=15, headers=HEADERS)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Could not fetch contact page %s: %s", url, exc)
            continue

        soup = BeautifulSoup(response.content, 'html.parser')
        text = soup.get_text('\n', strip=True)
        address = _extract_address_block_from_text(text)
        if address:
            return address

    return ''


def _resolve_university_address() -> str:
    live_address = _fetch_live_university_address()
    if live_address:
        return live_address

    for result in _curated_scraped_fallback(INTENT_CONTACT, max_results=3):
        address = _extract_address_block_from_text(result['text'])
        if address:
            return address

    page = _load_or_scrape_page(CANONICAL_CONTACT_URL)
    if page:
        return _extract_address_block_from_text(page.text)

    return ''


def _department_slug_variants(department: str) -> tuple[str, ...]:
    slug_map = _get_department_slugs()
    canonical = next((name for name in slug_map if name.lower() == department), '')
    return slug_map.get(canonical, ())


def _faculty_slug_variants(faculty: str) -> tuple[str, ...]:
    slug_map = _get_faculty_slugs()
    canonical = next((name for name in slug_map if name.lower() == faculty), '')
    return slug_map.get(canonical, ())


def _department_display_name(department: str) -> str:
    return next((name for name in _get_department_aliases() if name.lower() == department), department)


def _faculty_display_name(faculty: str) -> str:
    return next((name for name in _get_faculty_aliases() if name.lower() == faculty), faculty)


def _has_department_match(haystack: str, department: str) -> bool:
    if not department:
        return False
    alias_map = _get_department_aliases()
    slug_map = _get_department_slugs()
    canonical = next((name for name in alias_map if name.lower() == department), '')
    aliases = alias_map.get(canonical, ())
    slugs = slug_map.get(canonical, ())
    lowered = normalize_text(haystack)
    return (
        any(_contains_fuzzy_phrase(haystack, alias, threshold=0.84) for alias in aliases) or
        any(slug in lowered for slug in slugs)
    )


def _mentions_other_department(haystack: str, department: str) -> bool:
    lowered = normalize_text(haystack)
    slug_map = _get_department_slugs()
    for name, aliases in _get_department_aliases().items():
        if name.lower() == department:
            continue
        slugs = slug_map.get(name, ())
        if any(_contains_fuzzy_phrase(haystack, alias, threshold=0.84) for alias in aliases) or any(slug in lowered for slug in slugs):
            return True
    return False


def _format_course_code(code: str) -> str:
    code = re.sub(r'[\s-]+', '', (code or '').upper())
    match = re.match(r'^([A-Z]{2,4})(\d{3,4})$', code)
    if not match:
        return code
    return f"{match.group(1)} {match.group(2)}"


def _course_code_variants(code: str) -> set[str]:
    formatted = _format_course_code(code)
    compact = re.sub(r'[\s-]+', '', formatted)
    return {variant for variant in (formatted, compact) if variant}


def extract_named_entities(question: str) -> dict[str, list[str]]:
    normalized = normalize_text(question)
    faculty_hits = []
    dept_hits = _find_department_mentions(question)

    for label, aliases in _get_faculty_aliases().items():
        if any(_contains_fuzzy_phrase(normalized, alias, threshold=0.84) for alias in aliases):
            faculty_hits.append(label.lower())

    return {
        'course_codes': extract_course_codes(question),
        'faculties': list(dict.fromkeys(faculty_hits)),
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
INTENT_EXCHANGE     = 'exchange'

# Signals per intent — checked in priority order (more specific first)
_INTENT_SIGNALS: dict[str, list[str]] = {
    INTENT_BOLOGNA: [
        'bologna', 'obs.acibadem', 'akts', 'ects', 'ders planı', 'müfredat',
        'program çıktıları', 'öğrenme çıktıları', 'bilgi paketi',
        'mezuniyet koşulları', 'mezuniyet şartları', 'program yeterlilikleri',
        'yeterlilikleri', 'istihdam olanakları', 'alınacak derece',
        'çalışabilir', 'calisabilir', 'istihdam', 'kariyer',
        'mezun olunca', 'mezun iş', 'nerede çalış', 'nerede calis',
        'iş imkânı', 'is imkani', 'iş olanağı', 'is olanagi',
    ],
    INTENT_STAFF: [
        'akademik kadro', 'öğretim üyesi', 'hocalar', 'hoca', 'ogretim uyesi',
        'profesör', 'doçent', 'araştırma görevlisi', 'faculty members', 'kimler var',
    ],
    INTENT_ADMISSION: [
        'başvuru', 'kayıt', 'kabul', 'yks', 'tyt', 'ayt', 'puan',
        'kontenjan', 'taban puan', 'yerleşme', 'nasıl girilir',
        'admission', 'application', 'requirements', 'burs', 'ücret', 'harç',
        'fiyat', 'fiyatlar', 'ücretler', 'öğrenim ücreti', 'eğitim ücreti',
        'tuition fee', 'tuition fees', 'ne kadar', 'kaç para', 'maliyet',
        'burslu ücret', 'bursuz ücret', 'indirimli ücret',
    ],
    INTENT_EXCHANGE: [
        'erasmus', 'erasmus+', 'değişim programı', 'degisim programi',
        'exchange program', 'exchange programs', 'global exchange',
        'partner university', 'partner universities', 'institutional agreement',
        'institutional agreements', 'ikili anlaşma', 'ikili anlasma',
        'öğrenim hareketliliği', 'ogrenim hareketliligi',
        'staj hareketliliği', 'staj hareketliligi',
        'international office', 'uluslararası ofis', 'uluslararasi ofis',
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
    INTENT_BOLOGNA, INTENT_STAFF, INTENT_EXCHANGE, INTENT_ADMISSION, INTENT_CONTACT,
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
        INTENT_EXCHANGE,
        INTENT_CAMPUS, INTENT_STUDENT_LIFE,
    )

def is_campus_question(text: str) -> bool:
    return detect_intent(text) == INTENT_CAMPUS


def _is_transport_question(text: str) -> bool:
    lowered = _ascii_fold(text)
    return any(term in lowered for term in (
        'ulasim', 'ulasim nasil', 'nasil gidilir', 'nasıl gidilir',
        'metro', 'otobus', 'otobüs', 'servis', 'shuttle', 'transport',
    ))


# ===========================================================================
# URL STABILITY CLASSIFIER
# Scores URLs as stable/factual (+) or noisy/promotional (-)
# ===========================================================================

_URL_STABILITY_RULES: list[tuple[str, float, str]] = [
    # Highly stable factual pages — positive scores
    ('/institutional-agreements',    +0.26, 'exchange'),
    ('/exchange-programs',           +0.24, 'exchange'),
    ('/international-office',        +0.20, 'exchange'),
    ('/erasmus',                     +0.22, 'exchange'),
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
    INTENT_EXCHANGE:     ['exchange', 'admission', 'contact', 'about'],
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
    INTENT_EXCHANGE: [
        'erasmus', 'exchange programs', 'institutional agreements',
        'partner universities', 'partner countries', 'learning mobility',
        'traineeship mobility', 'international office', 'ikili anlaşmalar',
        'öğrenim hareketliliği', 'staj hareketliliği',
    ],
    INTENT_CONTACT: [
        'iletişim bilgileri', 'telefon numarası', 'e-posta adresi',
        'ofis adresi', 'nasıl ulaşılır', 'kampüs adresi',
        'kayışdağı', 'ataşehir', 'kerem aydınlar kampüsü',
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
    'erasmus': ['exchange', 'partner university', 'institutional agreements', 'country'],
    'değişim': ['exchange', 'erasmus', 'partner university', 'institutional agreements'],
    'degisim': ['exchange', 'erasmus', 'partner university', 'institutional agreements'],
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


def _looks_like_bologna_detail_question(question: str) -> bool:
    normalized = _ascii_fold(question)
    detail_terms = (
        'bologna', 'bilgi paketi', 'akts', 'ects', 'ders plani', 'mufredat',
        'mezuniyet kosullari', 'mezuniyet sartlari', 'program yeterlilikleri',
        'program cikt', 'ogrenme cikt', 'istihdam olanaklari', 'alinacak derece',
        'kabul kosullari', 'ust kademeye gecis',
        'calisabilir', 'istihdam', 'kariyer', 'is imkani', 'is olanagi',
        'nerede calis', 'mezun olunca', 'mezun is',
    )
    return any(term in normalized for term in detail_terms)


def _bologna_section_terms(question: str) -> list[str]:
    normalized = _ascii_fold(question)
    terms: list[str] = []
    if 'mezuniyet' in normalized:
        terms.extend(['Mezuniyet Koşulları', 'Mezuniyet Şartları'])
    if 'akts' in normalized or 'ects' in normalized:
        terms.extend(['Mezuniyet Koşulları', 'Toplam AKTS', 'AKTS'])
    if 'program yeterlilik' in normalized or 'yeterlilikleri' in normalized or 'program cikt' in normalized:
        terms.extend(['Program Yeterlilikleri', 'Program Çıktıları', 'Öğrenme Çıktıları'])
    if 'istihdam' in normalized or 'calisabilir' in normalized or 'çalışabilir' in normalized:
        terms.extend(['İstihdam Olanakları', 'Mezun İstihdam Olanakları'])
    if 'ders' in normalized or 'mufredat' in normalized or 'ders plani' in normalized:
        terms.extend(['Dersler', 'Ders Planı', 'Ders Kodu'])
    if 'kabul' in normalized:
        terms.extend(['Kabul Koşulları'])
    if 'derece' in normalized:
        terms.extend(['Alınacak Derece'])
    return list(dict.fromkeys(terms))


def _extract_bologna_section_snippet(content: str, question: str, max_chars: int = 2600) -> str:
    if not content:
        return ''

    search_terms = _bologna_section_terms(question) or extract_keywords(question)
    folded_content = _ascii_fold(content)
    windows: list[str] = []

    for term in search_terms:
        folded_term = _ascii_fold(term)
        idx = folded_content.find(folded_term)
        if idx < 0:
            continue

        # Prefer bounded Bologna sections when the scraper inserted section headers.
        section_start = content.rfind('--- ', 0, idx)
        start = section_start if section_start >= 0 and idx - section_start < 2500 else max(0, idx - 700)
        next_section = content.find('\n\n--- ', idx + len(term))
        end = next_section if next_section > idx else min(len(content), idx + 1500)
        windows.append(content[start:end].strip())
        if len(windows) >= 2:
            break

    if not windows:
        return extract_relevant_snippet(content, extract_keywords(question), max_chars=max_chars)

    snippet = '\n\n'.join(dict.fromkeys(windows))
    return snippet[:max_chars]


def _search_targeted_bologna_program_sections(
    question: str,
    department: str,
    max_results: int = 2,
) -> list[dict]:
    from scraper.models import BolognaProgram

    if not department:
        return []

    dept_display = _department_display_name(department)
    dept_folded = _ascii_fold(dept_display)
    question_folded = _ascii_fold(question)
    wants_english = 'ingilizce' in question_folded or 'ngilizce' in question_folded or 'english' in question_folded
    wants_graduate = any(term in question_folded for term in (
        'yuksek lisans', 'tezli yuksek lisans', 'doktora', 'master', 'phd',
    ))

    candidates = []
    for program in BolognaProgram.objects.exclude(content=''):
        haystack = f"{program.faculty} {program.department} {program.program_name}"
        if dept_folded not in _ascii_fold(haystack):
            continue
        if wants_english and 'ingilizce' not in _ascii_fold(haystack) and 'english' not in _ascii_fold(haystack):
            continue

        snippet = _extract_bologna_section_snippet(program.content, question)
        if not snippet:
            continue

        score = 0
        title_folded = _ascii_fold(haystack)
        if dept_folded in title_folded:
            score += 10
        if wants_english and ('ingilizce' in title_folded or 'english' in title_folded):
            score += 5
        if not wants_graduate:
            if 'lisans -' in title_folded and 'yuksek lisans' not in title_folded:
                score += 6
            if 'yuksek lisans' in title_folded or 'doktora' in title_folded:
                score -= 8
        elif 'yuksek lisans' in title_folded or 'doktora' in title_folded:
            score += 4
        if 'muhendislik ve doga bilimleri' in title_folded:
            score += 2
        if any(_ascii_fold(term) in _ascii_fold(snippet) for term in _bologna_section_terms(question)):
            score += 5

        candidates.append((score, program, snippet))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            'title': f"{program.faculty} - {program.program_name}",
            'text': snippet,
            'url': program.url,
        }
        for _, program, snippet in candidates[:max_results]
    ]


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
    if intent == INTENT_EXCHANGE:
        if any(term in url for term in (
            'international-office', 'exchange-programs', 'erasmus',
            'institutional-agreements', 'global-exchange',
        )):
            score += 45
        if any(term in title for term in (
            'erasmus', 'exchange', 'değişim', 'institutional agreements',
            'partner', 'international office',
        )):
            score += 30
        if any(term in text for term in (
            'erasmus', 'partner university', 'partner universities',
            'institutional agreements', 'ikili anlaşma', 'ikili anlaşmalar',
            'ülke', 'ülkeler', 'country', 'countries',
        )):
            score += 18
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
            'text': page.text[:40000],
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
    session = requests.Session()
    try:
        response = session.get(courses_url, timeout=20, headers=HEADERS)
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

        details = {
            'code': row_code,
            'name': cells[2] if len(cells) > 2 else '',
            'tul': cells[3] if len(cells) > 3 else '',
            'kind': cells[4] if len(cells) > 4 else '',
            'akts': cells[5] if len(cells) > 5 else '',
            'group_count': cells[6] if len(cells) > 6 else '',
            'teaching_mode': cells[7] if len(cells) > 7 else '',
            'url': courses_url,
        }
        detail = _fetch_bologna_course_detail(courses_url, soup, row, session=session)
        if detail:
            details.update(detail)
        return details

    return None


def _clean_bologna_detail_text(soup: BeautifulSoup, max_chars: int = 7000) -> str:
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'button', 'noscript']):
        tag.decompose()
    text = soup.get_text('\n', strip=True)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    clean_lines = []
    previous = ''
    noisy = {
        'Bilgi Paketi', 'ACU AI Asistan | Acıbadem Üniversitesi',
        'Yazdır', 'EN', 'TR',
    }
    for line in lines:
        if line == previous or line in noisy:
            continue
        clean_lines.append(line)
        previous = line
    return '\n'.join(clean_lines)[:max_chars]


def _hidden_form_payload(soup: BeautifulSoup) -> dict[str, str]:
    return {
        input_tag.get('name'): input_tag.get('value', '')
        for input_tag in soup.find_all('input')
        if input_tag.get('name')
    }


def _bologna_row_detail_event_target(row) -> str:
    link = row.find('a', id=lambda value: value and 'btnDersAyrinti' in value)
    if not link:
        return ''
    href = link.get('href', '')
    match = re.search(r"__doPostBack\('([^']+)'", href)
    return match.group(1) if match else ''


def _fetch_bologna_course_detail(
    courses_url: str,
    courses_soup: BeautifulSoup,
    row,
    session: requests.Session | None = None,
) -> dict:
    event_target = _bologna_row_detail_event_target(row)
    if not event_target:
        return {}

    post_data = _hidden_form_payload(courses_soup)
    post_data['__EVENTTARGET'] = event_target
    post_data['__EVENTARGUMENT'] = ''

    try:
        client = session or requests.Session()
        response = client.post(
            courses_url,
            data=post_data,
            headers={**HEADERS, 'Referer': courses_url},
            timeout=20,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Could not fetch Bologna course detail from %s: %s", courses_url, exc)
        return {}

    detail_soup = BeautifulSoup(response.content, 'html.parser')
    detail_text = _clean_bologna_detail_text(detail_soup)
    if not detail_text:
        return {}

    return {
        'detail_url': response.url,
        'detail_text': detail_text,
    }


def _extract_scraped_course_detail(content: str, course_code: str) -> tuple[str, str]:
    formatted_code = re.escape(_format_course_code(course_code))
    header_pattern = re.compile(
        rf'--- DERS BİLGİ PAKETİ:\s*{formatted_code}\s*-\s*(.*?)\s*---',
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = header_pattern.search(content or '')
    if not match:
        return '', ''

    start = match.start()
    next_match = re.search(r'\n\n--- DERS BİLGİ PAKETİ:', content[match.end():])
    end = match.end() + next_match.start() if next_match else len(content)
    section = content[start:end].strip()
    title = re.sub(r'\s+', ' ', match.group(1)).strip()
    return title, section


def _iter_scraped_course_detail_sections(content: str):
    header_pattern = re.compile(
        r'--- DERS BİLGİ PAKETİ:\s*([A-Z]{2,4}\s*\d{3,4})\s*-\s*(.*?)\s*---',
        flags=re.IGNORECASE | re.DOTALL,
    )
    matches = list(header_pattern.finditer(content or ''))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        code = _format_course_code(match.group(1))
        title = re.sub(r'\s+', ' ', match.group(2)).strip()
        yield code, title, content[start:end].strip()


def _source_url_from_scraped_course_detail(section: str, fallback_url: str) -> str:
    match = re.search(r'Kaynak:\s*(https?://\S+)', section or '')
    if not match:
        return fallback_url
    return match.group(1).rstrip('.,)')


def _scraped_course_detail_date_key(section: str) -> tuple[int, int, int]:
    matches = re.findall(r'\b(\d{2})\.(\d{2})\.(\d{4})\b', section or '')
    if not matches:
        return (0, 0, 0)
    dates = []
    for day, month, year in matches:
        dates.append((int(year), int(month), int(day)))
    return max(dates)


def _search_scraped_course_detail_pages(
    department: str,
    course_codes: list[str],
    max_results: int = 3,
) -> list[dict]:
    from scraper.models import BolognaProgram

    if not course_codes:
        return []

    display_name = _department_display_name(department) if department else ''
    aliases = _get_department_aliases().get(display_name, (display_name.lower(),)) if display_name else ()

    query = Q()
    code_query = Q()
    for code in course_codes:
        for variant in _course_code_variants(code):
            code_query |= Q(content__icontains=variant)
    query &= code_query

    if aliases:
        department_query = Q()
        for alias in aliases:
            department_query |= Q(program_name__icontains=alias)
            department_query |= Q(department__icontains=alias)
            department_query |= Q(faculty__icontains=alias)
        narrowed = list(BolognaProgram.objects.exclude(content='').filter(query & department_query))
        candidates = narrowed or list(BolognaProgram.objects.exclude(content='').filter(query))
    else:
        candidates = list(BolognaProgram.objects.exclude(content='').filter(query))

    if not candidates:
        return []

    if display_name and '(' not in display_name and 'ikinci' not in _ascii_fold(display_name):
        regular_candidates = [
            candidate for candidate in candidates
            if '(i' not in _ascii_fold(f"{candidate.department} {candidate.program_name}")
            and 'ikinci ogretim' not in _ascii_fold(f"{candidate.department} {candidate.program_name}")
        ]
        if regular_candidates:
            candidates = regular_candidates

    results_by_code: dict[str, list[dict]] = {}
    seen_sections = set()
    for program in candidates:
        for course_code in course_codes:
            course_name, section = _extract_scraped_course_detail(program.content, course_code)
            if not section:
                continue
            dedupe_key = (_format_course_code(course_code), course_name, section[:500])
            if dedupe_key in seen_sections:
                continue
            seen_sections.add(dedupe_key)
            result = {
                'url': _source_url_from_scraped_course_detail(section, program.url),
                'title': (
                    f"{program.faculty} - {program.program_name} "
                    f"{_format_course_code(course_code)} Ders Bilgisi"
                ).strip(),
                'text': section,
                '_date_key': _scraped_course_detail_date_key(section),
            }
            results_by_code.setdefault(_format_course_code(course_code), []).append(result)

    results = []
    for code in [_format_course_code(course_code) for course_code in course_codes]:
        code_results = results_by_code.get(code, [])
        code_results.sort(key=lambda item: item['_date_key'], reverse=True)
        results.extend(code_results[:1])

    for result in results:
        result.pop('_date_key', None)
    return results[:max_results]


def _course_name_query_terms(question: str) -> list[str]:
    terms = []
    for keyword in extract_keywords(question):
        folded = _ascii_fold(keyword)
        if len(folded) < 4:
            continue
        if folded in {
            'ders', 'dersi', 'dersin', 'dersinin', 'icerik', 'icerigi',
            'nedir', 'hangi', 'bilgi', 'paketi', 'akts', 'kredi',
        }:
            continue
        terms.append(keyword)
    return list(dict.fromkeys(terms))


def _search_scraped_course_detail_by_name(
    question: str,
    department: str = '',
    max_results: int = 3,
) -> list[dict]:
    from scraper.models import BolognaProgram

    terms = _course_name_query_terms(question)
    if not terms:
        return []

    display_name = _department_display_name(department) if department else ''
    aliases = _get_department_aliases().get(display_name, (display_name.lower(),)) if display_name else ()

    query = Q()
    for term in terms:
        query |= Q(content__icontains=term)

    if aliases:
        department_query = Q()
        for alias in aliases:
            department_query |= Q(program_name__icontains=alias)
            department_query |= Q(department__icontains=alias)
            department_query |= Q(faculty__icontains=alias)
        narrowed = list(BolognaProgram.objects.exclude(content='').filter(query & department_query))
        candidates = narrowed or list(BolognaProgram.objects.exclude(content='').filter(query))
    else:
        candidates = list(BolognaProgram.objects.exclude(content='').filter(query))

    folded_terms = [_ascii_fold(term) for term in terms]
    scored = []
    seen_sections = set()
    for program in candidates:
        for code, course_name, section in _iter_scraped_course_detail_sections(program.content):
            haystack = _ascii_fold(f"{code} {course_name}")
            score = sum(1 for term in folded_terms if term in haystack)
            if score <= 0:
                continue
            dedupe_key = (code, course_name, section[:500])
            if dedupe_key in seen_sections:
                continue
            seen_sections.add(dedupe_key)
            result = {
                'url': _source_url_from_scraped_course_detail(section, program.url),
                'title': f"{program.faculty} - {program.program_name} {code} Ders Bilgisi".strip(),
                'text': section,
                '_date_key': _scraped_course_detail_date_key(section),
            }
            scored.append((score, result))

    scored.sort(key=lambda item: (item[0], item[1]['_date_key']), reverse=True)
    results = []
    seen_codes = set()
    for _, result in scored:
        code_match = re.search(r'\b[A-Z]{2,4}\s+\d{3,4}\b', result['title'])
        code = code_match.group(0) if code_match else result['title']
        if code in seen_codes:
            continue
        seen_codes.add(code)
        result.pop('_date_key', None)
        results.append(result)
        if len(results) >= max_results:
            break

    return results


def _search_targeted_course_pages(
    department: str,
    course_codes: list[str],
    max_results: int = 3,
) -> list[dict]:
    from scraper.models import BolognaProgram

    if not department or not course_codes:
        return []

    display_name = _department_display_name(department)
    aliases = _get_department_aliases().get(display_name, (display_name.lower(),))

    query = Q()
    for alias in aliases:
        query |= Q(program_name__icontains=alias)
        query |= Q(department__icontains=alias)

    candidates = list(BolognaProgram.objects.filter(query))
    if not candidates:
        return []

    if '(' not in display_name and 'ikinci' not in _ascii_fold(display_name):
        regular_candidates = [
            candidate for candidate in candidates
            if '(i' not in _ascii_fold(f"{candidate.department} {candidate.program_name}")
            and 'ikinci ogretim' not in _ascii_fold(f"{candidate.department} {candidate.program_name}")
        ]
        if regular_candidates:
            candidates = regular_candidates

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
                '',
                details.get('detail_text', ''),
            ]).strip()
            results.append({
                'url': details.get('detail_url') or details['url'],
                'title': f"{display_name} {details['code']} Ders Bilgisi",
                'text': content,
            })
            if len(results) >= max_results:
                return results

    return results


def _is_fee_question(question: str) -> bool:
    normalized = normalize_text(question)
    return any(term in normalized for term in (
        'ucret', 'fiyat', 'ne kadar', 'kac para', 'maliyet',
        'tuition', 'burslu', 'bursuz', 'indirimli', 'harc',
    ))


def _search_targeted_fee_pages(question: str) -> list[dict]:
    keywords = extract_keywords(question)
    target_department = _primary_department(question)

    fee_urls = [
        'https://www.acibadem.edu.tr/aday/ogrenci/egitim/lisans/lisans-ogrenim-ucretleri-2025-2026',
        'https://www.acibadem.edu.tr/kayit/ucretler-ve-odeme-yontemleri',
        'https://www.acibadem.edu.tr/aday/ogrenci/egitim/burs/burs-olanaklari',
    ]

    results = []
    for url in fee_urls:
        page = _load_or_scrape_page(url)
        if not page or not page.text.strip():
            continue
        if target_department and 'ucretleri' in url:
            snippet = _extract_department_fee_section(page.text, target_department)
        else:
            snippet = extract_relevant_snippet(
                page.text,
                keywords + ['ucret', 'fiyat', 'burs', 'indirim', 'tl'],
                max_chars=3000,
            )
        if snippet:
            results.append({'url': page.url, 'title': page.title, 'text': snippet})
    return results


def _extract_department_fee_section(text: str, department: str) -> str:
    alias_map = _get_department_aliases()
    canonical = next((name for name in alias_map if name.lower() == department), department)
    aliases = list(alias_map.get(canonical, (canonical,)))

    lines = text.split('\n')
    start_idx = -1
    for i, line in enumerate(lines):
        normalized_line = normalize_text(line)
        if any(_contains_fuzzy_phrase(normalized_line, normalize_text(a), threshold=0.84) for a in aliases):
            start_idx = i
            break

    if start_idx == -1:
        return extract_relevant_snippet(text, aliases + ['ucret', 'tl'], max_chars=2000)

    section_lines = []
    for line in lines[max(0, start_idx - 1):start_idx + 8]:
        section_lines.append(line)

    return '\n'.join(section_lines)[:2000]


def _exchange_question_mentions_countries(question: str) -> bool:
    normalized = normalize_text(question)
    return any(term in normalized for term in (
        'hangi ülk', 'hangi ulk', 'ülke', 'ulke', 'ülkeler', 'ulkeler',
        'country', 'countries',
    ))


def _extract_department_erasmus_section(text: str, department: str, max_chars: int = 3500) -> str:
    """Extract the contiguous İkili Anlaşmalar table section for a specific department."""
    alias_map = _get_department_aliases()
    canonical = next((name for name in alias_map if name.lower() == department), department)
    aliases = list(alias_map.get(canonical, (canonical,)))

    lines = text.split('\n')
    start_idx = -1
    for i, line in enumerate(lines):
        normalized_line = normalize_text(line)
        if any(_contains_fuzzy_phrase(normalized_line, normalize_text(alias), threshold=0.84) for alias in aliases):
            start_idx = i
            break

    if start_idx == -1:
        return extract_relevant_snippet(text, aliases, max_chars=max_chars)

    # Section ends when another department/faculty header appears
    section_lines = [lines[start_idx]]
    dept_header_keywords = {
        normalize_text(alias)
        for aliases_list in alias_map.values()
        for alias in aliases_list
    }
    faculty_markers = ('fakülte', 'yüksekokul', 'enstitü', 'myo', 'fakulte', 'yuksekokul', 'enstitu')

    for line in lines[start_idx + 1:]:
        normalized_line = normalize_text(line)
        is_new_dept = any(_contains_fuzzy_phrase(normalized_line, kw, threshold=0.88) for kw in dept_header_keywords if len(kw) > 5)
        is_faculty_header = any(m in normalized_line for m in faculty_markers)
        if (is_new_dept or is_faculty_header) and normalized_line != normalize_text(lines[start_idx]):
            break
        section_lines.append(line)

    section = '\n'.join(section_lines)
    return section[:max_chars]


def _search_targeted_exchange_pages(question: str, max_results: int = 5) -> list[dict]:
    from scraper.models import URLIndex

    keywords = extract_keywords(expand_query(question, INTENT_EXCHANGE))
    target_department = _primary_department(question)
    asks_countries = _exchange_question_mentions_countries(question)

    query = Q()
    for term in (
        'international-office',
        'exchange-programs',
        'institutional-agreements',
        'global-exchange',
        'erasmus',
    ):
        query |= Q(url__icontains=term)
        query |= Q(title__icontains=term)
        query |= Q(path_keywords__icontains=term)

    indexed_urls: list[str] = []
    try:
        indexed_urls = list(URLIndex.objects.filter(query).values_list('url', flat=True)[:60])
    except Exception:
        indexed_urls = []

    candidate_urls = list(dict.fromkeys(indexed_urls + [
        'https://www.acibadem.edu.tr/uluslararasi-ofis/degisim-programlari/erasmus/ikili-anlasmalar',
        'https://www.acibadem.edu.tr/uluslararasi-ofis/degisim-programlari/erasmus/ogrenci-hareketliligi',
        'https://www.acibadem.edu.tr/uluslararasi-ofis/degisim-programlari/global-degisim-programlari',
        'https://www.acibadem.edu.tr/uluslararasi-ofis/degisim-programlari/erasmus/koordinatorler-listesi',
        'https://www.acibadem.edu.tr/en/international-office/exchange-programs/erasmus/erasmus-agreements',
        'https://www.acibadem.edu.tr/en/international-office/exchange-programs/erasmus/student-mobility',
        'https://www.acibadem.edu.tr/en/international-office/about',
        'https://www.acibadem.edu.tr/en/international-office/exchange-programs',
        'https://www.acibadem.edu.tr/en/international-office/exchange-programs/erasmus',
        'https://www.acibadem.edu.tr/en/international-office/exchange-programs/global-exchange-programs',
        'https://www.acibadem.edu.tr/en/international-office/institutional-agreements',
    ]))

    scored_results: list[tuple[int, dict]] = []
    for url in candidate_urls:
        page = _load_or_scrape_page(url)
        if not page:
            continue

        haystack = normalize_text(f"{page.title} {page.url} {page.text[:6000]}")
        score = 0

        for kw in keywords:
            score += haystack.count(kw) * 3
            score += normalize_text(page.url).count(kw) * 4
            score += normalize_text(page.title).count(kw) * 5

        if any(term in haystack for term in (
            'erasmus', 'exchange program', 'exchange programs',
            'değişim program', 'degisim program',
        )):
            score += 30
        if any(term in haystack for term in (
            'institutional agreement', 'institutional agreements',
            'partner university', 'partner universities',
            'ikili anlaşma', 'ikili anlaşmalar',
        )):
            score += 24
        if asks_countries and any(term in haystack for term in (
            'country', 'countries', 'ülke', 'ülkeler', 'partner university',
        )):
            score += 18
        if target_department:
            if _has_department_match(haystack, target_department):
                score += 35
            elif _mentions_other_department(haystack, target_department):
                score -= 10
        if 'international-office' in normalize_text(page.url):
            score += 10

        if score <= 0:
            continue

        is_agreements_page = any(s in page.url for s in ('ikili-anlasmalar', 'erasmus-agreements'))
        if is_agreements_page and target_department:
            snippet = _extract_department_erasmus_section(page.text, target_department, max_chars=3500)
        else:
            snippet = extract_relevant_snippet(
                page.text,
                keywords + ['erasmus', 'exchange', 'partner', 'country'],
                max_chars=2600,
            )
        scored_results.append((
            score,
            {
                'url': page.url,
                'title': page.title,
                'text': snippet,
            },
        ))

    scored_results.sort(key=lambda item: item[0], reverse=True)
    return [result for _, result in scored_results[:max_results]]


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
            Q(url__icontains='ucret') |
            Q(url__icontains='tuition') |
            Q(title__icontains='Başvuru') |
            Q(title__icontains='Burs') |
            Q(title__icontains='Ücret') |
            Q(title__icontains='Tuition')
        )
    elif intent == INTENT_EXCHANGE:
        query = (
            Q(url__icontains='international-office') |
            Q(url__icontains='exchange-programs') |
            Q(url__icontains='institutional-agreements') |
            Q(url__icontains='erasmus') |
            Q(title__icontains='Erasmus') |
            Q(title__icontains='Exchange') |
            Q(title__icontains='Institutional Agreements')
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

    results = []
    for p in ranked_pages[:max_results]:
        kws = extract_keywords(p.title + ' ' + p.url)
        if intent == INTENT_CONTACT:
            kws = list(dict.fromkeys(kws + [
                'adres', 'kayışdağı', 'ataşehir', 'kampüs', 'no:32',
                'telefon', 'iletişim', 'e-posta',
            ]))
        results.append({
            'url': p.url,
            'title': p.title,
            'text': extract_relevant_snippet(p.text, kws, max_chars=2200),
        })
    return results


def _search_transport_pages(max_results: int = 3) -> list[dict]:
    from scraper.models import ScrapedPage

    query = (
        Q(url__icontains='ulasim') |
        Q(url__icontains='transport') |
        Q(title__icontains='Ulaşım') |
        Q(title__icontains='Transportation') |
        Q(title__icontains='Transport')
    )

    pages = list(
        ScrapedPage.objects.filter(query)
        .exclude(url__icontains='/etkinlikler/')
        .exclude(url__icontains='/duyurular/')
        .exclude(url__icontains='/haberler/')[:20]
    )

    keywords = [
        'ulaşım', 'ulasim', 'transport', 'transportation',
        'metro', 'kozyatağı', 'kozyatagi', 'otobüs', 'otobus',
        'servis', 'shuttle', 'araç', 'car', 'adres', 'kayışdağı',
        'kayisdagi', 'ataşehir', 'atasehir', 'no:32',
    ]
    ranked_pages = sorted(
        pages,
        key=lambda page: score_page_relevance(page.title + ' ' + page.url, page, keywords),
        reverse=True,
    )

    results = []
    for page in ranked_pages[:max_results]:
        results.append({
            'url': page.url,
            'title': page.title,
            'text': extract_relevant_snippet(page.text, keywords, max_chars=2600),
        })
    return results


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

    if intent == INTENT_EXCHANGE:
        for result in _search_targeted_exchange_pages(question, max_results=8):
            page_type = classify_page_type(result['url'], result['title'], result['text'])
            candidates.append({
                'text': result['text'],
                'title': result['title'],
                'url': result['url'],
                'source_type': 'scraped',
                'distance': 0.28,
                'page_type': page_type,
                'section_type': 'exchange',
                'faculty': '',
                'department': '',
                'course_code': '',
                'language': 'en' if '/en/' in result['url'] else 'tr',
                'last_updated': '',
                'is_stable': page_type not in _NOISY_PAGE_TYPES,
                'is_noisy': page_type in _NOISY_PAGE_TYPES,
            })

    if intent in (INTENT_CAMPUS, INTENT_CONTACT, INTENT_ADMISSION, INTENT_STUDENT_LIFE, INTENT_EXCHANGE):
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
        variants = {variant.lower() for variant in _course_code_variants(code)}
        compact_meta_code = re.sub(r'[\s-]+', '', meta['course_code'].lower())
        if (
            any(variant in text or variant in title for variant in variants)
            or compact_meta_code in variants
        ):
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
    if intent == INTENT_CONTACT:
        text_lower = chunk.get('text', '').lower()
        if any(kw in text_lower for kw in ('kayışdağı', 'kayisdagi', 'ataşehir', 'atasehir', 'no:32')):
            boost += 0.40
    if intent == INTENT_ADMISSION and page_type == 'admission':
        boost += 0.25
    if intent == INTENT_EXCHANGE and page_type == 'exchange':
        boost += 0.34
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

    if _is_general_university_address_question(question):
        logger.info("[RETRIEVAL] canonical university address lookup")
        address = _resolve_university_address()
        if address:
            context_parts.append(f"=== Acıbadem Üniversitesi Adres ===\n{address}")
            sources.append(CANONICAL_CONTACT_URL)
        for result in _curated_scraped_fallback(INTENT_CONTACT, max_results=2):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if _is_transport_question(question):
        logger.info("[RETRIEVAL] targeted transport lookup")
        address = _resolve_university_address()
        if address:
            context_parts.append(f"=== Acıbadem Üniversitesi Adres ===\n{address}")
            sources.append(CANONICAL_CONTACT_URL)
        for result in _search_transport_pages(max_results=3):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if is_head_question(question) and target_department:
        logger.info("[RETRIEVAL] targeted head lookup for department=%s", target_department)
        for result in search_scraped_pages(question, max_results=3):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if entities['course_codes']:
        effective_dept = target_department or _department_from_course_codes(entities['course_codes'])
        logger.info(
            "[RETRIEVAL] targeted course lookup for department=%s course_codes=%s",
            effective_dept or 'any',
            entities['course_codes'],
        )
        course_results = _search_scraped_course_detail_pages(
            effective_dept,
            entities['course_codes'],
            max_results=3,
        )
        if not course_results and effective_dept:
            course_results = _search_targeted_course_pages(
                effective_dept,
                entities['course_codes'],
                max_results=3,
            )
        for result in course_results:
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if intent == INTENT_COURSE:
        effective_dept = target_department
        logger.info(
            "[RETRIEVAL] targeted course-name lookup for department=%s",
            effective_dept or 'any',
        )
        for result in _search_scraped_course_detail_by_name(
            question,
            effective_dept,
            max_results=3,
        ):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if intent == INTENT_STAFF and target_department:
        logger.info("[RETRIEVAL] targeted staff lookup for department=%s", target_department)
        for result in search_scraped_pages(question, max_results=1):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if intent == INTENT_CONTACT:
        logger.info("[RETRIEVAL] targeted contact/address lookup")
        contact_results = _curated_scraped_fallback(INTENT_CONTACT, max_results=3)
        for result in contact_results:
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if intent == INTENT_EXCHANGE:
        logger.info("[RETRIEVAL] targeted exchange lookup")
        for result in _search_targeted_exchange_pages(question, max_results=5):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if len(context_parts) >= 2:
            return '\n\n'.join(context_parts), sources

    if intent == INTENT_ADMISSION and _is_fee_question(question):
        logger.info("[RETRIEVAL] targeted fee lookup")
        for result in _search_targeted_fee_pages(question):
            if result['url'] in sources:
                continue
            context_parts.append(f"=== {result['title']} ===\n{result['text']}")
            sources.append(result['url'])
        if context_parts:
            return '\n\n'.join(context_parts), sources

    if target_department and (intent == INTENT_BOLOGNA or _looks_like_bologna_detail_question(question)):
        logger.info(
            "[RETRIEVAL] targeted Bologna section lookup for department=%s",
            target_department,
        )
        for result in _search_targeted_bologna_program_sections(question, target_department, max_results=1):
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
        elif intent in (INTENT_ADMISSION, INTENT_CONTACT, INTENT_ANNOUNCEMENT, INTENT_EXCHANGE):
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
                        INTENT_CAMPUS, INTENT_STUDENT_LIFE, INTENT_EXCHANGE):
            if intent == INTENT_EXCHANGE:
                for result in _search_targeted_exchange_pages(question, max_results=5):
                    if result['url'] not in sources:
                        context_parts.append(f"=== {result['title']} ===\n{result['text']}")
                        sources.append(result['url'])

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
