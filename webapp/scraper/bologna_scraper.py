import time
import logging
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

logger = logging.getLogger(__name__)

BASE_URL = 'https://obs.acibadem.edu.tr/oibs/bologna/'
HEADERS = {
    'User-Agent': 'ACU-ChatBot/1.0 (Educational Project - CSE322)',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
    'Referer': 'https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=tr',
}

PROGRAM_TYPES = {
    'lis': 'Lisans',
    'myo': 'Ön Lisans',
    'yls': 'Yüksek Lisans',
    'dok': 'Doktora',
}

# General info pages: pageId -> (title, faculty_label)
GENERAL_PAGES = {
    100: ('Yönetim', 'Kurumsal Bilgiler'),
    101: ('Üniversite Hakkında', 'Kurumsal Bilgiler'),
    102: ('Bologna Komisyonu', 'Kurumsal Bilgiler'),
    103: ('İletişim ve Ulaşım', 'Kurumsal Bilgiler'),
    104: ('AKTS Kataloğu', 'Kurumsal Bilgiler'),
    300: ('Şehir Hakkında - İstanbul', 'Öğrenci Bilgileri'),
    301: ('Kampüs', 'Öğrenci Bilgileri'),
    302: ('Yemek Hizmetleri', 'Öğrenci Bilgileri'),
    303: ('Sağlık Hizmetleri', 'Öğrenci Bilgileri'),
    304: ('Spor ve Sosyal Yaşam', 'Öğrenci Bilgileri'),
    305: ('Öğrenci Kulüpleri', 'Öğrenci Bilgileri'),
    309: ('Konaklama', 'Öğrenci Bilgileri'),
    311: ('Engelli Öğrenci Hizmetleri', 'Öğrenci Bilgileri'),
    400: ('Bologna Süreci', 'Bologna Bilgileri'),
}

PROGRAM_SECTION_PAGES = (
    ('Eğitim Türü (Amaçlar) ve Hedefler', 'progGoalsObjectives.aspx'),
    ('Program Hakkında', 'progAbout.aspx'),
    ('Program Profili', 'progProfile.aspx'),
    ('Program Yetkilileri', 'progOfficials.aspx'),
    ('Alınacak Derece', 'progDegree.aspx'),
    ('Kabul Koşulları', 'progAdmissionReq.aspx'),
    ('Üst Kademeye Geçiş', 'progAccessFurhterStudies.aspx'),
    ('Mezuniyet Koşulları', 'progGraduationReq.aspx'),
    ('Önceki Öğrenmenin Tanınması', 'progRecogPriorLearning.aspx'),
    ('Yeterlilik Koşulları ve Kuralları', 'progQualifyReqReg.aspx'),
    ('İstihdam Olanakları', 'progOccupationalProf.aspx'),
    ('Program Yeterlilikleri', 'progLearnOutcomes.aspx'),
    ('Dersler', 'progCourses.aspx'),
    ('Ders & Program Yeterlilikleri İlişkisi', 'progCourseMatrix.aspx'),
    ('TYYÇ - Program Yeterlilikleri İlişkisi', 'progTYYCMatrix.aspx'),
    ('Akademik Personel', 'progAcademicStaff.aspx'),
    ('İletişim', 'progContact.aspx'),
)


def fetch_html(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return BeautifulSoup(r.content, 'html.parser')
    except requests.RequestException as e:
        logger.warning(f"Fetch failed: {url} — {e}")
        return None


def clean_text(soup: BeautifulSoup) -> str:
    text = soup.get_text(separator='\n', strip=True)
    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 2]
    return sanitize_db_text('\n'.join(lines))


def sanitize_db_text(text: str) -> str:
    """PostgreSQL text fields cannot store NUL bytes from malformed page content."""
    return (text or '').replace('\x00', '')


def _hidden_form_payload(soup: BeautifulSoup) -> dict[str, str]:
    return {
        input_tag.get('name'): input_tag.get('value', '')
        for input_tag in soup.find_all('input')
        if input_tag.get('name')
    }


def _course_row_event_target(row) -> str:
    link = row.find('a', id=lambda value: value and 'btnDersAyrinti' in value)
    if not link:
        return ''
    href = link.get('href', '')
    match = re.search(r"__doPostBack\('([^']+)'", href)
    return match.group(1) if match else ''


def scrape_course_detail_pages(courses_url: str, soup: BeautifulSoup) -> list[str]:
    """
    Fetch every clickable course detail page from a program's Dersler table.
    The OBS page uses ASP.NET postbacks; posting the row target redirects to
    progCourseDetails.aspx?curCourse=...
    """
    session = requests.Session()
    try:
        response = session.get(courses_url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
    except requests.RequestException as exc:
        logger.warning("Could not refresh course list before detail scraping %s: %s", courses_url, exc)

    payload = _hidden_form_payload(soup)
    details: list[str] = []
    seen_targets: set[str] = set()
    seen_urls: set[str] = set()

    for row in soup.find_all('tr'):
        cells = [cell.get_text(' ', strip=True) for cell in row.find_all('td')]
        if len(cells) < 6:
            continue

        course_code = cells[1] if len(cells) > 1 else ''
        course_name = cells[2] if len(cells) > 2 else ''
        if not course_code or not course_name:
            continue

        event_target = _course_row_event_target(row)
        if not event_target or event_target in seen_targets:
            continue
        seen_targets.add(event_target)

        post_data = dict(payload)
        post_data['__EVENTTARGET'] = event_target
        post_data['__EVENTARGUMENT'] = ''

        try:
            response = session.post(
                courses_url,
                data=post_data,
                headers={**HEADERS, 'Referer': courses_url},
                timeout=20,
                allow_redirects=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Course detail fetch failed for %s %s: %s", course_code, course_name, exc)
            continue

        if response.url in seen_urls:
            continue
        seen_urls.add(response.url)

        detail_soup = BeautifulSoup(response.content, 'html.parser')
        text = clean_text(detail_soup)
        if len(text) < 80:
            continue

        details.append(
            '\n'.join([
                f"--- DERS BİLGİ PAKETİ: {course_code} - {course_name} ---",
                f"Kaynak: {response.url}",
                text,
            ])
        )
        time.sleep(0.4)

    return details


def get_all_program_links() -> list[dict]:
    """
    Fetch all programs from all degree types via unitSelection.aspx.
    Returns list of dicts with name, url, faculty, program_type, curSunit, curUnit.
    """
    programs = []

    for prog_type, type_name in PROGRAM_TYPES.items():
        url = BASE_URL + f'unitSelection.aspx?type={prog_type}&lang=tr'
        soup = fetch_html(url)
        if not soup:
            logger.warning(f"Could not fetch {type_name} program list")
            continue

        current_faculty = ''
        type_count = 0

        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            text = a.text.strip()

            if not text:
                continue

            # Faculty headers link to in-page anchors like #x12, #x01, etc.
            if href.startswith('#x') and len(href) <= 5:
                current_faculty = text
                continue

            # Program links — these have showPac + curSunit
            if 'showPac' in href and 'curSunit' in href:
                full_url = urljoin(BASE_URL, href)
                parsed = urlparse(full_url)
                params = parse_qs(parsed.query)
                cur_sunit = params.get('curSunit', [''])[0]
                cur_unit = params.get('curUnit', [''])[0]

                if cur_sunit:
                    programs.append({
                        'name': text,
                        'url': full_url,
                        'faculty': current_faculty,
                        'program_type': type_name,
                        'curSunit': cur_sunit,
                        'curUnit': cur_unit,
                    })
                    type_count += 1

        logger.info(f"Found {type_count} {type_name} programs")

    return programs


def scrape_program_content(cur_sunit: str) -> str:
    """
    Scrape all available content for a program:
    - progAbout.aspx: program details (mission, description, outcomes)
    - progCourses.aspx: full course/curriculum list
    No character limit — store everything for accurate Q&A.
    """
    parts = []

    for section_title, endpoint in PROGRAM_SECTION_PAGES:
        url = BASE_URL + f'{endpoint}?lang=tr&curSunit={cur_sunit}'
        soup = fetch_html(url)
        if not soup:
            continue

        text = clean_text(soup)
        if len(text) > 50:
            parts.append(f'--- {section_title.upper()} ---\nKaynak: {url}\n{text}')

        if endpoint == 'progCourses.aspx':
            parts.extend(scrape_course_detail_pages(url, soup))

        time.sleep(0.4)

    return '\n\n'.join(parts)


def scrape_general_pages():
    """
    Scrape general university info pages (campus, food, sports, contact, etc.)
    and save them as BolognaProgram entries.
    """
    from scraper.models import BolognaProgram

    created = 0
    updated = 0

    for page_id, (title, category) in GENERAL_PAGES.items():
        url = BASE_URL + f'dynConPage.aspx?curPageId={page_id}&lang=tr'
        soup = fetch_html(url)
        if not soup:
            continue

        content = clean_text(soup)
        if len(content) < 50:
            logger.warning(f"  Empty general page: {title} (id={page_id})")
            continue

        _, is_new = BolognaProgram.objects.update_or_create(
            url=url,
            defaults={
                'faculty': sanitize_db_text(category)[:300],
                'department': sanitize_db_text(title)[:300],
                'program_name': sanitize_db_text(title)[:300],
                'content': sanitize_db_text(content),
            }
        )
        logger.info(f"  General page saved: {title} ({len(content)} chars)")
        if is_new:
            created += 1
        else:
            updated += 1

        time.sleep(1)

    logger.info(f"General pages: {created} new, {updated} updated")


def _has_complete_program_content(content: str) -> bool:
    content = content or ''
    return (
        '--- PROGRAM HAKKINDA ---' in content and
        '--- DERSLER ---' in content and
        '--- DERS BİLGİ PAKETİ:' in content
    )


def scrape_all_bologna(skip_existing: bool = False, start_at: int = 1):
    """
    Main entry point. Uses requests+BeautifulSoup — no Selenium needed.
    Scrapes:
      1. All academic programs (progAbout + progCourses per program)
      2. All general university info pages (campus, food, sports, contact, etc.)
    """
    from scraper.models import BolognaProgram

    logger.info("Bologna scraper starting (requests-based, no Selenium)...")

    # --- Step 1: General info pages ---
    logger.info("Scraping general university info pages...")
    scrape_general_pages()

    # --- Step 2: Academic programs ---
    programs = get_all_program_links()
    total = len(programs)
    logger.info(f"Total academic programs to scrape: {total}")

    if not programs:
        logger.warning("No programs found — check unitSelection.aspx URLs")
        return

    created = 0
    updated = 0

    for i, program in enumerate(programs, start=1):
        if i < start_at:
            logger.info(f"[{i}/{total}] Skipping before start_at={start_at}")
            continue

        name = program['name']
        url = program['url']
        faculty = program['faculty']
        cur_sunit = program['curSunit']
        prog_type = program['program_type']

        logger.info(f"[{i}/{total}] {prog_type} | {faculty} | {name}")

        if skip_existing:
            existing = BolognaProgram.objects.filter(url=url).only('content').first()
            if existing and _has_complete_program_content(existing.content):
                logger.info(f"  Already has full Bologna content, skipping: {name}")
                continue

        content = scrape_program_content(cur_sunit)

        if not content or len(content) < 50:
            logger.warning(f"  No content, skipping: {name}")
            continue

        _, is_new = BolognaProgram.objects.update_or_create(
            url=url,
            defaults={
                'faculty': sanitize_db_text(faculty)[:300],
                'department': sanitize_db_text(name)[:300],
                'program_name': sanitize_db_text(f"{prog_type} - {name}")[:300],
                'content': sanitize_db_text(content),
            }
        )

        if is_new:
            created += 1
        else:
            updated += 1

        time.sleep(1.5)  # Responsible delay between requests

    logger.info(f"Academic programs done. {created} new, {updated} updated.")
    logger.info(f"Total Bologna DB entries: {BolognaProgram.objects.count()}")
