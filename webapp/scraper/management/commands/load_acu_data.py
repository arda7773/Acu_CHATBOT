import json
import os

from django.core.management.base import BaseCommand
from scraper.models import ScrapedPage


class Command(BaseCommand):
    help = 'Load pre-scraped ACU data from acu_data.json into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            default='acu_data.json',
            help='Path to the JSON file (default: acu_data.json)',
        )

    def handle(self, *args, **options):
        filepath = options['file']

        if not os.path.exists(filepath):
            self.stdout.write(self.style.WARNING(
                f'File not found: {filepath} — skipping ACU data load.'
            ))
            return

        self.stdout.write(f'Loading ACU data from {filepath}...')

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        pages = data.get('pages', [])
        created = 0
        updated = 0

        for page in pages:
            url = page.get('url', '').strip()
            if not url:
                continue

            _, is_new = ScrapedPage.objects.update_or_create(
                url=url,
                defaults={
                    'title': page.get('title', '')[:500],
                    'description': page.get('description', ''),
                    'keywords': page.get('keywords', ''),
                    'text': page.get('text', ''),
                    'lang': page.get('lang', ''),
                    'source': page.get('source', 'acibadem_main'),
                    'depth': page.get('depth', 0),
                    'scraped_at': page.get('scraped_at', ''),
                }
            )
            if is_new:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done! {created} new pages, {updated} updated. Total: {created + updated}'
        ))
