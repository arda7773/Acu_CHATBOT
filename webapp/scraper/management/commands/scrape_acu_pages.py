from django.core.management.base import BaseCommand

from scraper.acu_scraper import scrape_acu_pages


class Command(BaseCommand):
    help = 'Scrape all ACU pages from sitemap.xml and save them to the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Optionally limit the number of sitemap URLs to scrape',
        )
        parser.add_argument(
            '--url-contains',
            default=None,
            help='Scrape only URLs containing this substring',
        )

    def handle(self, *args, **options):
        limit = options['limit']
        url_contains = options['url_contains']
        self.stdout.write('Starting ACU website scrape...')
        stats = scrape_acu_pages(limit=limit, url_contains=url_contains)
        self.stdout.write(self.style.SUCCESS(
            'Done! '
            f"indexed={stats['indexed']} "
            f"created={stats['created']} "
            f"updated={stats['updated']} "
            f"failed={stats['failed']}"
        ))
