from django.core.management.base import BaseCommand
from scraper.bologna_scraper import scrape_all_bologna
from scraper.models import BolognaProgram


class Command(BaseCommand):
    help = 'Scrape all Bologna academic programs from obs.acibadem.edu.tr (requests-based, no Selenium)'

    def handle(self, *args, **options):
        self.stdout.write('Starting Bologna scraper...')
        scrape_all_bologna()
        count = BolognaProgram.objects.count()
        self.stdout.write(self.style.SUCCESS(f'Bologna scraping completed! DB now has {count} programs.'))
