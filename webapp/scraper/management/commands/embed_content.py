"""
embed_content — chunk all scraped content and generate pgvector embeddings.

Usage:
    python manage.py embed_content                  # both sources
    python manage.py embed_content --source scraped
    python manage.py embed_content --source bologna
    python manage.py embed_content --reset          # wipe and rebuild
"""
import time
import logging

from django.core.management.base import BaseCommand

from scraper.embedder import chunk_text, get_embedding
from scraper.content_metadata import build_chunk_metadata
from scraper.models import ContentChunk

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Chunk all scraped/Bologna content and generate pgvector embeddings'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            choices=['scraped', 'bologna', 'all'],
            default='all',
            help='Which source to embed (default: all)',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing chunks for the chosen source before rebuilding',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of chunks to bulk-insert at once (default: 50)',
        )

    def handle(self, *args, **options):
        source = options['source']
        reset = options['reset']
        batch_size = options['batch_size']

        if source in ('scraped', 'all'):
            self._embed_scraped(reset=reset, batch_size=batch_size)

        if source in ('bologna', 'all'):
            self._embed_bologna(reset=reset, batch_size=batch_size)

        total = ContentChunk.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'\nDone! Total chunks in DB: {total}'
        ))

    # ------------------------------------------------------------------
    def _embed_scraped(self, reset: bool, batch_size: int):
        from scraper.models import ScrapedPage

        if reset:
            deleted, _ = ContentChunk.objects.filter(source_type=ContentChunk.SOURCE_SCRAPED).delete()
            self.stdout.write(f'Deleted {deleted} existing scraped chunks.')

        pages = list(ScrapedPage.objects.exclude(text='').order_by('id'))
        self.stdout.write(f'Embedding {len(pages)} scraped pages...')

        self._process_records(
            records=pages,
            source_type=ContentChunk.SOURCE_SCRAPED,
            get_text=lambda p: p.text,
            get_title=lambda p: p.title or p.url,
            get_url=lambda p: p.url,
            get_language=lambda p: p.lang,
            get_faculty=lambda p: '',
            get_department=lambda p: '',
            get_scraped_at=lambda p: p.scraped_at,
            batch_size=batch_size,
        )

    def _embed_bologna(self, reset: bool, batch_size: int):
        from scraper.models import BolognaProgram

        if reset:
            deleted, _ = ContentChunk.objects.filter(source_type=ContentChunk.SOURCE_BOLOGNA).delete()
            self.stdout.write(f'Deleted {deleted} existing Bologna chunks.')

        programs = list(BolognaProgram.objects.exclude(content='').order_by('id'))
        self.stdout.write(f'Embedding {len(programs)} Bologna programs...')

        self._process_records(
            records=programs,
            source_type=ContentChunk.SOURCE_BOLOGNA,
            get_text=lambda p: p.content,
            get_title=lambda p: f"{p.faculty} - {p.program_name}",
            get_url=lambda p: p.url,
            get_language=lambda p: 'tr',
            get_faculty=lambda p: p.faculty,
            get_department=lambda p: p.department,
            get_scraped_at=lambda p: p.scraped_at.isoformat() if p.scraped_at else '',
            batch_size=batch_size,
        )

    # ------------------------------------------------------------------
    def _process_records(
        self,
        records,
        source_type,
        get_text,
        get_title,
        get_url,
        get_language,
        get_faculty,
        get_department,
        get_scraped_at,
        batch_size,
    ):
        buffer = []
        ok = skip = fail = 0

        for i, record in enumerate(records, start=1):
            title = get_title(record)
            url = get_url(record)
            text = get_text(record)
            language = get_language(record)
            faculty = get_faculty(record)
            department = get_department(record)
            scraped_at = get_scraped_at(record)

            chunks = chunk_text(text)
            if not chunks:
                skip += 1
                continue

            for idx, chunk in enumerate(chunks):
                metadata = build_chunk_metadata(
                    source_type=source_type,
                    url=url,
                    title=title,
                    text=chunk,
                    language=language,
                    faculty=faculty,
                    department=department,
                    scraped_at=scraped_at,
                )
                if metadata.is_noisy and metadata.page_type in {'announcement', 'event', 'news', 'promo'}:
                    skip += 1
                    continue

                embedding = get_embedding(chunk)
                if embedding is None:
                    fail += 1
                    continue

                buffer.append(ContentChunk(
                    source_type=source_type,
                    source_url=url,
                    title=title,
                    chunk_text=chunk,
                    chunk_index=idx,
                    page_type=metadata.page_type,
                    section_type=metadata.section_type,
                    faculty=metadata.faculty[:300],
                    department=metadata.department[:300],
                    course_code=metadata.course_code[:32],
                    language=metadata.language[:10],
                    last_updated=metadata.last_updated,
                    is_stable=metadata.is_stable,
                    is_noisy=metadata.is_noisy,
                    embedding=embedding,
                ))
                ok += 1

                if len(buffer) >= batch_size:
                    ContentChunk.objects.bulk_create(buffer, ignore_conflicts=True)
                    buffer.clear()

            # Progress every 10 records
            if i % 10 == 0:
                self.stdout.write(
                    f'  [{i}/{len(records)}] ok={ok} skip={skip} fail={fail}',
                    ending='\r',
                )
                self.stdout.flush()

            time.sleep(0.05)  # be gentle with Ollama

        if buffer:
            ContentChunk.objects.bulk_create(buffer, ignore_conflicts=True)

        self.stdout.write(
            f'\n  Finished: ok={ok} skip={skip} fail={fail}'
        )
