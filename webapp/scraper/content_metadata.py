import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse, parse_qs


BOILERPLATE_LINE_PATTERNS = [
    r'^anasayfa$',
    r'^home$',
    r'^tanıtım kataloğu$',
    r'^tanitim katalogu$',
    r'^sanal tur$',
    r'^sor$',
    r'^cevaplayalım$',
    r'^cevaplayalim$',
    r'^detaylı bilgi için tıklayın$',
    r'^detayli bilgi icin tiklayin$',
    r'^üniversite introduction booklet$',
    r'^university introduction booklet$',
]

NOISY_TEXT_PATTERNS = [
    'tanıtım kataloğu sanal tur başlangıç tarihi',
    'tanitim katalogu sanal tur baslangic tarihi',
    'başlangıç tarihi:',
    'bitiş tarihi:',
    'zoom üzerinden',
]

FACULTY_KEYWORDS = {
    'tıp fakültesi': 'Tıp Fakültesi',
    'tip fakultesi': 'Tıp Fakültesi',
    'eczacılık fakültesi': 'Eczacılık Fakültesi',
    'eczacilik fakultesi': 'Eczacılık Fakültesi',
    'sağlık bilimleri fakültesi': 'Sağlık Bilimleri Fakültesi',
    'saglik bilimleri fakultesi': 'Sağlık Bilimleri Fakültesi',
    'mühendislik ve doğa bilimleri fakültesi': 'Mühendislik ve Doğa Bilimleri Fakültesi',
    'muhendislik ve doga bilimleri fakultesi': 'Mühendislik ve Doğa Bilimleri Fakültesi',
    'fen edebiyat fakültesi': 'Fen Edebiyat Fakültesi',
    'fen edebiyat fakultesi': 'Fen Edebiyat Fakültesi',
    'lisansüstü eğitim enstitüsü': 'Lisansüstü Eğitim Enstitüsü',
    'lisansustu egitim enstitusu': 'Lisansüstü Eğitim Enstitüsü',
    'meslek yüksekokulu': 'Meslek Yüksekokulu',
}

DEPARTMENT_KEYWORDS = {
    'psikoloji': 'Psikoloji',
    'hemşirelik': 'Hemşirelik',
    'hemsirelik': 'Hemşirelik',
    'bilgisayar mühendisliği': 'Bilgisayar Mühendisliği',
    'bilgisayar muhendisligi': 'Bilgisayar Mühendisliği',
    'moleküler biyoloji ve genetik': 'Moleküler Biyoloji ve Genetik',
    'molekuler biyoloji ve genetik': 'Moleküler Biyoloji ve Genetik',
    'tıp': 'Tıp',
    'tip': 'Tıp',
    'eczacılık': 'Eczacılık',
    'eczacilik': 'Eczacılık',
    'beslenme ve diyetetik': 'Beslenme ve Diyetetik',
}


@dataclass
class ChunkMetadata:
    source_type: str
    page_type: str
    section_type: str
    faculty: str
    department: str
    course_code: str
    language: str
    last_updated: datetime | None
    is_stable: bool
    is_noisy: bool


def normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').lower()).strip()


def classify_page_type(url: str, title: str = '', text: str = '') -> str:
    haystack = f"{normalize_text(url)} {normalize_text(title)} {normalize_text(text)[:800]}"

    rules = [
        (('duyurular', 'announcement'), 'announcement'),
        (('etkinlikler', 'event'), 'event'),
        (('haberler', 'news'), 'news'),
        (('iletisim', 'contact', 'telefon', 'e-posta', 'email'), 'contact'),
        (('kampus', 'yerleske', 'yerleşke', 'kampüs olanak', 'surdurulebilir-kampus', 'sürdürülebilir kampüs'), 'campus'),
        (('ogrenci yasami', 'öğrenci yaşamı', 'kulup', 'kulüp', 'life at acu'), 'student_life'),
        (('akademik-kadro', 'academic staff', 'öğretim üyesi', 'akademik kadro'), 'staff'),
        (('basvuru', 'admission', 'kabul', 'burs', 'kontenjan'), 'admission'),
        (('ders planı', 'ders plani', 'course', 'curriculum', 'akts', 'ects'), 'course'),
        (('bolumler', 'bölümler', 'department', 'programlar', 'faculty'), 'department'),
        (('sanal tur', 'tanıtım', 'tanitim'), 'promo'),
    ]
    for needles, page_type in rules:
        if any(needle in haystack for needle in needles):
            return page_type

    if 'obs.acibadem.edu.tr' in url:
        return 'academic'
    return 'general'


def infer_section_type(url: str, source_type: str, page_type: str) -> str:
    path = urlparse(url).path.lower()
    if source_type == 'bologna':
        return 'bologna'
    if '/aday/' in path or '/uluslararasi-ofis/' in path:
        return 'admissions'
    if '/akademik/' in path:
        return 'academic'
    if '/universite/' in path:
        return 'university'
    if '/duyurular/' in path or '/etkinlikler/' in path:
        return 'news'
    return page_type


def infer_language(url: str, text: str = '', default: str = 'tr') -> str:
    path = urlparse(url).path.lower()
    if '/en/' in path or '/english/' in path:
        return 'en'
    sample = normalize_text(text)[:500]
    if sample and all(ord(ch) < 128 for ch in sample if ch.isalpha()) and ' the ' in f' {sample} ':
        return 'en'
    return default


def infer_faculty(title: str, text: str, url: str) -> str:
    haystack = f"{normalize_text(title)} {normalize_text(text)[:1200]} {normalize_text(url)}"
    for needle, label in FACULTY_KEYWORDS.items():
        if needle in haystack:
            return label
    return ''


def infer_department(title: str, text: str, url: str) -> str:
    haystack = f"{normalize_text(title)} {normalize_text(text)[:1200]} {normalize_text(url)}"
    for needle, label in DEPARTMENT_KEYWORDS.items():
        if needle in haystack:
            return label
    return ''


def extract_course_code(text: str, title: str = '', url: str = '') -> str:
    haystack = f"{title}\n{text}\n{url}"
    match = re.search(r'\b([A-Z]{2,4}\s?-?\s?\d{3,4})\b', haystack)
    if match:
        return re.sub(r'\s+', '', match.group(1)).replace('-', '')
    return ''


def infer_last_updated(scraped_at: str = '', text: str = '', url: str = '') -> datetime | None:
    for value in (scraped_at, text[:400], url):
        if not value:
            continue
        match = re.search(r'(\d{4})[-/.](\d{2})[-/.](\d{2})', value)
        if match:
            year, month, day = map(int, match.groups())
            try:
                return datetime(year, month, day)
            except ValueError:
                pass
        match = re.search(r'(\d{2})[./](\d{2})[./](\d{4})', value)
        if match:
            day, month, year = map(int, match.groups())
            try:
                return datetime(year, month, day)
            except ValueError:
                pass

    if 'obs.acibadem.edu.tr' in url:
        try:
            cur_unit = parse_qs(urlparse(url).query).get('curUnit')
            if cur_unit:
                return None
        except Exception:
            return None
    return None


def is_noisy_content(text: str, url: str = '', title: str = '') -> bool:
    text_norm = normalize_text(text)
    url_norm = normalize_text(url)
    title_norm = normalize_text(title)

    if any(pattern in text_norm for pattern in NOISY_TEXT_PATTERNS):
        return True
    if 'etkinlikler' in url_norm and len(text_norm.split()) < 80:
        return True
    if 'duyurular' in url_norm and len(text_norm.split()) < 80:
        return True
    if title_norm.startswith('kampüs buluşmaları'):
        return True
    return False


def is_stable_page(page_type: str, url: str) -> bool:
    if page_type in {'announcement', 'event', 'news', 'promo'}:
        return False
    path = urlparse(url).path.lower()
    if any(noisy in path for noisy in ('/duyurular/', '/etkinlikler/', '/haberler/')):
        return False
    return True


def clean_scraped_text(text: str) -> str:
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    cleaned: list[str] = []
    seen = set()

    for line in lines:
        line_norm = normalize_text(line)
        if not line_norm:
            continue
        if any(re.match(pattern, line_norm) for pattern in BOILERPLATE_LINE_PATTERNS):
            continue
        if line_norm in seen:
            continue
        seen.add(line_norm)
        cleaned.append(line)

    compact = '\n'.join(cleaned)
    compact = re.sub(r'\n{3,}', '\n\n', compact).strip()
    return compact


def build_chunk_metadata(
    *,
    source_type: str,
    url: str,
    title: str,
    text: str,
    language: str = 'tr',
    faculty: str = '',
    department: str = '',
    scraped_at: str = '',
) -> ChunkMetadata:
    page_type = classify_page_type(url, title, text)
    resolved_faculty = faculty or infer_faculty(title, text, url)
    resolved_department = department or infer_department(title, text, url)
    course_code = extract_course_code(text, title, url)
    last_updated = infer_last_updated(scraped_at=scraped_at, text=text, url=url)
    resolved_language = infer_language(url, text, default=language or 'tr')
    is_noisy = is_noisy_content(text, url, title)
    return ChunkMetadata(
        source_type=source_type,
        page_type=page_type,
        section_type=infer_section_type(url, source_type, page_type),
        faculty=resolved_faculty,
        department=resolved_department,
        course_code=course_code,
        language=resolved_language,
        last_updated=last_updated,
        is_stable=is_stable_page(page_type, url),
        is_noisy=is_noisy,
    )
