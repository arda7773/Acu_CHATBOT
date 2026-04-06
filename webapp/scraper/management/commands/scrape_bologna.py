from django.core.management.base import BaseCommand
from scraper.bologna_scraper import scrape_all_bologna


class Command(BaseCommand):
    help = 'Scrape all Bologna academic programs from obs.acibadem.edu.tr using Selenium'

    def handle(self, *args, **options):
        self.stdout.write('Starting Bologna scraper (this may take several minutes)...')
        scrape_all_bologna()
        self.stdout.write(self.style.SUCCESS('Bologna scraping completed!'))
