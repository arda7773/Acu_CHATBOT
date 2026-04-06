import re
import logging
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SITEMAP_URL = 'https://www.acibadem.edu.tr/sitemap.xml'
HEADERS = {
    'User-Agent': 'ACU-ChatBot-Indexer/1.0 (Educational Project - CSE322)'
}


def extract_keywords_from_url(url: str) -> str:
    """Extract readable keywords from URL path segments."""
    path = urlparse(url).path
    # Replace separators with spaces
    keywords = re.sub(r'[-_/]', ' ', path)
    # Remove file extensions
    keywords = re.sub(r'\.\w+$', '', keywords)
    # Collapse whitespace
    keywords = ' '.join(keywords.split()).lower()
    return keywords


def get_category_from_url(url: str) -> str:
    """Derive a category label from the first path segment."""
    path = urlparse(url).path.strip('/')
    parts = path.split('/')
    return parts[0] if parts and parts[0] else 'genel'


def fetch_all_urls_from_sitemap(sitemap_url: str) -> list[str]:
    """
    Recursively fetch all page URLs from a sitemap or sitemap index.
    Handles both <sitemap> (index) and <url> (regular) formats.
    """
    urls = []
    try:
        resp = requests.get(sitemap_url, timeout=30, headers=HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch sitemap {sitemap_url}: {e}")
        return urls

    soup = BeautifulSoup(resp.content, 'xml')

    # Sitemap index — contains links to other sitemaps
    sub_sitemaps = soup.find_all('sitemap')
    if sub_sitemaps:
        for sm in sub_sitemaps:
            loc = sm.find('loc')
            if loc and loc.text:
                urls.extend(fetch_all_urls_from_sitemap(loc.text.strip()))
        return urls

    # Regular sitemap — contains <url><loc> entries
    for loc in soup.find_all('loc'):
        url = loc.text.strip()
        if url:
            urls.append(url)

    return urls


def index_sitemap() -> int:
    """
    Main entry point: fetch sitemap, extract all URLs,
    derive keywords from paths, and store in URLIndex.
    Returns number of newly created entries.
    """
    from scraper.models import URLIndex

    logger.info(f"Starting sitemap indexing from {SITEMAP_URL}")
    urls = fetch_all_urls_from_sitemap(SITEMAP_URL)
    logger.info(f"Found {len(urls)} URLs in sitemap")

    created_count = 0
    for url in urls:
        path_keywords = extract_keywords_from_url(url)
        category = get_category_from_url(url)

        _, created = URLIndex.objects.get_or_create(
            url=url,
            defaults={
                'path_keywords': path_keywords,
                'category': category,
            }
        )
        if created:
            created_count += 1

    logger.info(f"Sitemap indexing done. {created_count} new URLs added.")
    return created_count
