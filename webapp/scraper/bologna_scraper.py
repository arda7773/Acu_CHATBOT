import time
import logging
from urllib.parse import urljoin

from django.conf import settings
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)

BOLOGNA_INDEX_URL = 'https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=tr'


def get_driver() -> webdriver.Remote:
    """Create a Selenium Remote WebDriver connected to the selenium container."""
    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=tr')

    driver = webdriver.Remote(
        command_executor=settings.SELENIUM_URL,
        options=options,
    )
    driver.implicitly_wait(10)
    return driver


def get_all_program_links(driver) -> list[dict]:
    """
    Open the Bologna index page and collect all program links.
    Returns a list of dicts: {name, url, faculty}
    """
    programs = []
    logger.info(f"Opening Bologna index: {BOLOGNA_INDEX_URL}")
    driver.get(BOLOGNA_INDEX_URL)
    time.sleep(5)  # Allow JS to render

    try:
        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, 'a')))
    except Exception:
        logger.warning("Timed out waiting for Bologna index to load")

    seen_hrefs = set()
    candidate_frames = [None]
    candidate_frames.extend(driver.find_elements(By.TAG_NAME, 'iframe'))
    logger.info(f"Checking Bologna DOM across {len(candidate_frames)} frame context(s)")

    selectors = [
        'a[href*="showPac"]',
        'a[href*="showProgram"]',
        'a[href*="curOp=show"]',
        'a[href*="bologna"]',
        'a[href*="curSunit"]',
        'a[href*="ders"]',
        'a[href*="program"]',
        '[onclick*="showPac"]',
        '[onclick*="showProgram"]',
        '[onclick*="curOp=show"]',
    ]

    for frame in candidate_frames:
        try:
            driver.switch_to.default_content()
            if frame is not None:
                driver.switch_to.frame(frame)
                time.sleep(1)
        except Exception as e:
            logger.warning(f"Could not switch frame while collecting Bologna links: {e}")
            continue

        for selector in selectors:
            for link in driver.find_elements(By.CSS_SELECTOR, selector):
                href = link.get_attribute('href') or ''
                onclick = link.get_attribute('onclick') or ''
                text = link.text.strip()
                candidate_url = href.strip()

                if not candidate_url and 'location' in onclick:
                    parts = onclick.split("'")
                    candidate_url = next((p for p in parts if 'show' in p or 'program' in p), '')

                if not candidate_url or not text:
                    continue

                if not candidate_url.startswith('http'):
                    candidate_url = urljoin('https://obs.acibadem.edu.tr/', candidate_url)

                if candidate_url not in seen_hrefs:
                    seen_hrefs.add(candidate_url)
                    programs.append({'name': text, 'url': candidate_url, 'faculty': ''})

        if not programs:
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            for link in soup.select('a[href], [onclick]'):
                href = (link.get('href') or '').strip()
                onclick = (link.get('onclick') or '').strip()
                text = link.get_text(' ', strip=True)
                candidate_url = href or onclick
                if not text:
                    continue
                if not any(token in candidate_url.lower() for token in ['show', 'program', 'bologna', 'curop']):
                    continue
                if not candidate_url.startswith('http'):
                    candidate_url = urljoin('https://obs.acibadem.edu.tr/', candidate_url)
                if candidate_url not in seen_hrefs:
                    seen_hrefs.add(candidate_url)
                    programs.append({'name': text, 'url': candidate_url, 'faculty': ''})

    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    logger.info(f"Found {len(programs)} program links")
    return programs


def extract_faculty_name(content: str) -> str:
    """
    Attempt to extract faculty name from the first lines of scraped content.
    Bologna pages typically list the faculty/school near the top.
    """
    for line in content.split('\n')[:30]:
        line = line.strip()
        lower = line.lower()
        if any(kw in lower for kw in ['fakülte', 'faculty', 'enstitü', 'institute',
                                       'yüksekokul', 'school', 'meslek']):
            return line[:300]
    return 'Acıbadem Üniversitesi'


def scrape_program_page(driver, url: str) -> str:
    """Navigate to a program page and extract its full text content."""
    try:
        driver.get(url)
        time.sleep(2)

        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, 'body')))

        body = driver.find_element(By.TAG_NAME, 'body')
        raw_text = body.text

        lines = [l.strip() for l in raw_text.split('\n') if l.strip() and len(l.strip()) > 2]
        return '\n'.join(lines)[:6000]

    except Exception as e:
        logger.error(f"Failed to scrape {url}: {e}")
        return ''


def scrape_all_bologna():
    """
    Main entry point for Bologna scraping.
    Discovers all program pages and saves them to BolognaProgram model.
    """
    from scraper.models import BolognaProgram

    logger.info("Bologna scraper starting...")
    driver = None

    try:
        driver = get_driver()
        programs = get_all_program_links(driver)

        if not programs:
            logger.warning("No program links found — check Bologna index page structure")
            return

        for i, program in enumerate(programs, start=1):
            url = program['url']
            name = program['name']
            logger.info(f"[{i}/{len(programs)}] Scraping: {name}")

            content = scrape_program_page(driver, url)
            if not content:
                logger.warning(f"No content extracted for {name}, skipping")
                continue

            faculty = extract_faculty_name(content)

            BolognaProgram.objects.update_or_create(
                url=url,
                defaults={
                    'faculty': faculty,
                    'department': name,
                    'program_name': name,
                    'content': content,
                }
            )
            logger.info(f"Saved: {name} ({faculty})")
            time.sleep(2)  # Respectful delay between requests

        logger.info(f"Bologna scraping complete. {len(programs)} programs processed.")

    except Exception as e:
        logger.error(f"Bologna scraper encountered a fatal error: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()
