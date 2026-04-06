from django.core.management.base import BaseCommand
from scraper.sitemap_indexer import index_sitemap


class Command(BaseCommand):
    help = 'Index all ACU website URLs from sitemap.xml into URLIndex table'

    def handle(self, *args, **options):
        self.stdout.write('Fetching and indexing ACU sitemap...')
        count = index_sitemap()
        self.stdout.write(self.style.SUCCESS(f'Done! {count} new URLs indexed.'))
