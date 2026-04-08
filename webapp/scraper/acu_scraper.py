import logging
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from django.utils import timezone

from scraper.sitemap_indexer import (
    HEADERS as INDEX_HEADERS,
    extract_keywords_from_url,
    fetch_all_urls_from_sitemap,
    get_category_from_url,
)
from scraper.content_metadata import clean_scraped_text

logger = logging.getLogger(__name__)

SITEMAP_URL = 'https://www.acibadem.edu.tr/sitemap.xml'
HEADERS = {
    **INDEX_HEADERS,
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
}
SKIP_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.rar', '.mp4', '.mp3',
}
DYNAMIC_URL_HINTS = (
    '/akademik-kadro',
    '/academic-staff',
    '/bilgi-paketi',
)


def should_scrape_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'}:
        return False
    if parsed.netloc != 'www.acibadem.edu.tr':
        return False

    lower_path = parsed.path.lower()
    return not any(lower_path.endswith(ext) for ext in SKIP_EXTENSIONS)


def extract_metadata(soup: BeautifulSoup) -> tuple[str, str, str]:
    title = ''
    title_tag = soup.find('title')
    if title_tag and title_tag.text:
        title = title_tag.text.strip()[:500]

    description = ''
    description_tag = soup.find('meta', attrs={'name': 'description'})
    if description_tag:
        description = (description_tag.get('content') or '').strip()

    keywords = ''
    keywords_tag = soup.find('meta', attrs={'name': 'keywords'})
    if keywords_tag:
        keywords = (keywords_tag.get('content') or '').strip()

    return title, description, keywords


def extract_clean_text(soup: BeautifulSoup) -> str:
    for tag in soup([
        'nav', 'footer', 'script', 'style', 'header', 'aside',
        'form', 'button', 'noscript', 'svg'
    ]):
        tag.decompose()

    main = (
        soup.find('main') or
        soup.find(id='content') or
        soup.find(id='main-content') or
        soup.find(class_='content') or
        soup.find(class_='main-content') or
        soup.find('article') or
        soup.find('body')
    )
    if not main:
        return ''

    text = main.get_text(separator='\n', strip=True)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return clean_scraped_text('\n'.join(lines))


def needs_dynamic_render(url: str, soup: BeautifulSoup, clean_text: str) -> bool:
    lower_url = url.lower()
    if any(hint in lower_url for hint in DYNAMIC_URL_HINTS):
        return True
    if soup.select('[data-block-ek-id]'):
        return True
    return (
        len(clean_text) < 120
        and 'Akademik Kadro' in clean_text
        and 'Bilgi Paketi' in clean_text
    )


def extract_dynamic_page_text(url: str, timeout: int = 25) -> str:
    from scraper.bologna_scraper import get_driver

    driver = None
    try:
        driver = get_driver()
        driver.get(url)
        time.sleep(4)
        rendered_soup = BeautifulSoup(driver.page_source, 'html.parser')
        return extract_clean_text(rendered_soup)
    except Exception as exc:
        logger.warning("Dynamic render failed for %s: %s", url, exc)
        return ''
    finally:
        if driver:
            driver.quit()


def extract_page_payload(url: str, timeout: int = 20) -> dict | None:
    try:
        response = requests.get(url, timeout=timeout, headers=HEADERS)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
    except requests.RequestException as exc:
        logger.warning("Failed to fetch ACU page %s: %s", url, exc)
        return None

    soup = BeautifulSoup(response.content, 'html.parser')
    title, description, keywords = extract_metadata(soup)
    clean_text = extract_clean_text(soup)

    if needs_dynamic_render(url, soup, clean_text):
        logger.info("Dynamic page detected, using Selenium render for %s", url)
        dynamic_text = extract_dynamic_page_text(url)
        if dynamic_text:
            clean_text = clean_scraped_text(dynamic_text)

    if not clean_text:
        logger.warning("No main content found for %s", url)
        return None

    if len(clean_text) < 80:
        logger.info("Skipping short/empty ACU page %s", url)
        return None

    return {
        'url': url,
        'title': title,
        'description': description,
        'keywords': keywords,
        'text': clean_text[:20000],
        'lang': 'tr',
        'source': 'acibadem_main',
        'depth': max(0, len([p for p in urlparse(url).path.split('/') if p])),
        'path_keywords': extract_keywords_from_url(url),
        'category': get_category_from_url(url),
        'last_fetched': timezone.now(),
    }


def scrape_acu_pages(
    sitemap_url: str = SITEMAP_URL,
    limit: int | None = None,
    url_contains: str | None = None,
    delay_seconds: float = 0.2,
) -> dict[str, int]:
    from scraper.models import ScrapedPage, URLIndex

    urls = [url for url in fetch_all_urls_from_sitemap(sitemap_url) if should_scrape_url(url)]
    if url_contains:
        urls = [url for url in urls if url_contains.lower() in url.lower()]
    if limit is not None:
        urls = urls[:limit]

    stats = {
        'indexed': 0,
        'created': 0,
        'updated': 0,
        'skipped': 0,
        'failed': 0,
    }

    logger.info("Starting ACU page scrape for %s URLs", len(urls))
    for idx, url in enumerate(urls, start=1):
        logger.info("[%s/%s] Scraping ACU page: %s", idx, len(urls), url)
        payload = extract_page_payload(url)
        if not payload:
            stats['failed'] += 1
            continue

        URLIndex.objects.update_or_create(
            url=url,
            defaults={
                'title': payload['title'],
                'path_keywords': payload['path_keywords'],
                'category': payload['category'],
                'last_fetched': payload['last_fetched'],
            },
        )
        stats['indexed'] += 1

        _, created = ScrapedPage.objects.update_or_create(
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
        if created:
            stats['created'] += 1
        else:
            stats['updated'] += 1

        if delay_seconds:
            time.sleep(delay_seconds)

    stats['skipped'] = max(0, len(urls) - stats['created'] - stats['updated'] - stats['failed'])
    logger.info("ACU page scrape finished: %s", stats)
    return stats
