from django.core.management.base import BaseCommand
from scraper.bologna_scraper import scrape_all_bologna
from scraper.models import BolognaProgram


class Command(BaseCommand):
    help = 'Scrape all Bologna academic programs from obs.acibadem.edu.tr (requests-based, no Selenium)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip programs that already have full scraped Bologna content.',
        )
        parser.add_argument(
            '--start-at',
            type=int,
            default=1,
            help='Start scraping at this 1-based program index from the discovered program list.',
        )

    def handle(self, *args, **options):
        self.stdout.write('Starting Bologna scraper...')
        scrape_all_bologna(
            skip_existing=options['skip_existing'],
            start_at=max(1, options['start_at']),
        )
        count = BolognaProgram.objects.count()
        self.stdout.write(self.style.SUCCESS(f'Bologna scraping completed! DB now has {count} programs.'))
