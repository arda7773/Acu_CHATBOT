import re
import time
import logging
from collections import Counter

import requests
from bs4 import BeautifulSoup
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

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


def normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text.lower()).strip()


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


def extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from a question, including Turkish stems."""
    text = text.lower()
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
    return keywords


def is_staff_question(text: str) -> bool:
    normalized = normalize_text(text)
    return any(
        phrase in normalized
        for phrase in ['akademik kadro', 'hocalar', 'hoca', 'ogretim uyesi', 'öğretim üyesi']
    )


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
    if not keywords:
        return []

    query = Q()
    for kw in keywords:
        query |= Q(faculty__icontains=kw)
        query |= Q(department__icontains=kw)
        query |= Q(program_name__icontains=kw)
        query |= Q(content__icontains=kw)

    candidates = list(BolognaProgram.objects.filter(query))

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

    keywords = extract_keywords(question)
    if not keywords:
        return []
    asks_for_staff = is_staff_question(question)
    listing_q = is_listing_question(question)
    asks_for_bolum = any(kw in ('bölüm', 'bolum', 'bölümler', 'bolumler', 'program', 'programlar') for kw in keywords)

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


def get_context_for_question(question: str) -> tuple[str, list[str]]:
    """
    Given a user question, return (context_text, source_urls).

    Priority:
    1. Bologna DB (OBS) — academic programs + general university info (campus, food, etc.)
       If Bologna has results → use ONLY Bologna. It's the authoritative curated source.
    2. ScrapedPage DB (acibadem.edu.tr) — fallback when Bologna has nothing.
    3. Real-time fetch — last resort if both DBs are empty.
    """
    context_parts = []
    sources = []
    listing = is_listing_question(question)

    # Step 1: Bologna DB — always check first, it's the priority source
    bologna_max = 4 if listing else 2
    bologna_results = search_bologna(question, max_results=bologna_max)
    for result in bologna_results:
        context_parts.append(
            f"=== {result['faculty']} - {result['program']} ===\n"
            f"{result['content']}"
        )
        if result['url'] not in sources:
            sources.append(result['url'])

    # Step 2: ScrapedPage DB — only if Bologna found nothing
    if not context_parts:
        scraped_max = 4 if listing else 2
        scraped_results = search_scraped_pages(question, max_results=scraped_max)
        for result in scraped_results:
            if result['url'] not in sources:
                context_parts.append(
                    f"=== {result['title']} ===\n{result['text']}"
                )
                sources.append(result['url'])

    # Step 3: Real-time fallback — only if both DBs are empty
    if not context_parts:
        logger.info("No DB results found, falling back to real-time fetch")
        rt_max = 3 if listing else 2
        urls = find_relevant_urls(question, max_results=rt_max)
        for url in urls:
            if url in sources:
                continue
            text = fetch_page_text(url)
            if text:
                context_parts.append(f"=== Kaynak: {url} ===\n{text}")
                sources.append(url)
            time.sleep(0.5)

    # Step 4: Absolute fallback — serve general university info pages
    # Triggered when all keywords were stop words (e.g. "üniversite hakkında bilgi ver")
    if not context_parts:
        logger.info("No keywords matched — serving default university info pages")
        from scraper.models import BolognaProgram
        defaults = BolognaProgram.objects.filter(
            program_name__in=['Üniversite Hakkında', 'Kampüs', 'Bologna Süreci']
        )
        for p in defaults:
            context_parts.append(f"=== {p.faculty} - {p.program_name} ===\n{p.content}")
            sources.append(p.url)

    context = '\n\n'.join(context_parts)
    return context, sources
